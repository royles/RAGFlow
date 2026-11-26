"""Tests for chunk spans and the chunk preview API."""

import logging
import os
import tempfile
import unittest

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend.accesslog import FirstRootRequestOnly, install
from backend.chunking import chunk_spans, chunk_text
from backend.vectors import close_client
from tests import fake_openai
from tests.helpers import Server, shared_app_server


class SpanTests(unittest.TestCase):
    def test_neighbours_share_exactly_the_overlap(self):
        spans = chunk_spans("x" * 1000, 400, 100)
        self.assertEqual(spans, [(0, 400), (300, 700), (600, 1000)])
        for (_, end), (start, _) in zip(spans, spans[1:]):
            self.assertEqual(end - start, 100)

    def test_chunk_text_matches_spans(self):
        text = "alpha beta gamma delta epsilon zeta eta theta"
        pieces = [text[s:e].strip() for s, e in chunk_spans(text, 12, 3)]
        self.assertEqual(chunk_text(text, 12, 3), pieces)

    def test_empty_text_has_no_spans(self):
        self.assertEqual(chunk_spans("  \n", 100, 10), [])


class AccessLogTests(unittest.TestCase):
    def record(self, method, path, status):
        return logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("127.0.0.1:1", method, path, "1.1", status), None)

    def test_only_the_first_root_request_is_logged(self):
        filt = FirstRootRequestOnly()
        self.assertTrue(filt.filter(self.record("GET", "/", 200)))
        self.assertFalse(filt.filter(self.record("GET", "/", 200)))
        self.assertFalse(filt.filter(self.record("GET", "/", 200)))

    def test_other_requests_are_always_logged(self):
        filt = FirstRootRequestOnly()
        filt.filter(self.record("GET", "/", 200))
        for method, path, status in (("GET", "/api/health", 200), ("POST", "/", 200), ("GET", "/", 404), ("GET", "/", 500)):
            self.assertTrue(filt.filter(self.record(method, path, status)), (method, path, status))

    def test_installed_once_on_the_uvicorn_access_logger(self):
        install()
        install()
        filters = [f for f in logging.getLogger("uvicorn.access").filters if isinstance(f, FirstRootRequestOnly)]
        self.assertEqual(len(filters), 1)


class PreviewApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app_server()
        cls.fake = Server(fake_openai.app).start()

    @classmethod
    def tearDownClass(cls):
        cls.fake.stop()

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        self.http = httpx.Client(base_url=self.app.url, timeout=30)

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def use_llm(self, base_url):
        response = self.http.put(
            "/api/models",
            json={
                "openai": {"base_url": base_url, "api_key": "k"},
                "llm": {"provider": "openai", "model": "fake-chat"},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_builtin_sample_is_about_500_words_on_rag(self):
        body = self.http.post("/api/chunk-preview/sample", json={"use_llm": True}).json()
        self.assertEqual(body["source"], "builtin")
        self.assertIn("No LLM", body["note"])
        self.assertTrue(450 <= len(body["text"].split()) <= 550)
        self.assertIn("retrieval-augmented generation", body["text"].lower())

    def test_llm_writes_the_sample_when_asked(self):
        self.use_llm(self.fake.url + "/v1")
        fake_openai.calls.clear()
        body = self.http.post("/api/chunk-preview/sample", json={"use_llm": True}).json()
        self.assertEqual((body["source"], body["model"]), ("llm", "fake-chat"))
        prompt = fake_openai.calls[-1]["body"]["messages"][0]["content"]
        self.assertIn("500 words", prompt)
        self.assertIn("retrieval-augmented generation", prompt)

    def test_llm_is_not_called_unless_asked(self):
        self.use_llm(self.fake.url + "/v1")
        fake_openai.calls.clear()
        body = self.http.post("/api/chunk-preview/sample", json={"use_llm": False}).json()
        self.assertEqual(body["source"], "builtin")
        self.assertEqual(fake_openai.calls, [])

    def test_llm_failure_falls_back_with_a_note(self):
        self.use_llm("http://127.0.0.1:1/v1")
        body = self.http.post("/api/chunk-preview/sample", json={"use_llm": True}).json()
        self.assertEqual(body["source"], "builtin")
        self.assertIn("could not write a sample", body["note"])
        self.assertNotIn("\"k\"", body["note"])

    def test_preview_returns_spans_that_match_indexing(self):
        text = self.http.post("/api/chunk-preview/sample", json={}).json()["text"]
        body = self.http.post("/api/chunk-preview", json={"text": text, "chunk_size": 800, "chunk_overlap": 120}).json()
        spans = [(c["start"], c["end"]) for c in body["chunks"]]
        self.assertEqual(spans, chunk_spans(text, 800, 120))
        self.assertEqual(body["text"], text.strip())
        self.assertGreater(len(spans), 2)

    def test_preview_rejects_bad_settings(self):
        for body in (
            {"text": "hello", "chunk_size": 200, "chunk_overlap": 200},
            {"text": "hello", "chunk_size": 50, "chunk_overlap": 0},
            {"text": "  ", "chunk_size": 200, "chunk_overlap": 0},
            {"text": "x" * 9000, "chunk_size": 200, "chunk_overlap": 0},
        ):
            self.assertEqual(self.http.post("/api/chunk-preview", json=body).status_code, 400, body)


if __name__ == "__main__":
    unittest.main()
