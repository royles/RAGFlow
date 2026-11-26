"""Tests for chunking, Bedrock request bodies, the HTTP API, and MCP."""

import asyncio
import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import httpx
from docx import Document

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend.bedrock import embedding_request
from backend.chunking import chunk_text
from backend.embeddings import local_embed
from backend.settings import max_chunks
from backend.vectors import close_client
from tests.helpers import shared_app_server


class ChunkTests(unittest.TestCase):
    def test_windows_overlap(self):
        chunks = chunk_text("abcdefghij", size=4, overlap=1)
        self.assertEqual(chunks, ["abcd", "defg", "ghij"])

    def test_overlap_must_be_smaller_than_the_window(self):
        with self.assertRaises(ValueError):
            chunk_text("abcdef", size=4, overlap=4)


class EmbeddingTests(unittest.TestCase):
    def test_local_vectors_are_stable_and_normalized(self):
        first = local_embed("Dublin warehouse code")
        second = local_embed("Dublin warehouse code")
        self.assertEqual(first, second)
        self.assertEqual(len(first), 384)
        norm = sum(value * value for value in first) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=5)

    def test_titan_and_cohere_request_bodies(self):
        titan = embedding_request("amazon.titan-embed-text-v2:0", "hello", 1024, "document")
        self.assertEqual(titan["dimensions"], 1024)
        self.assertTrue(titan["normalize"])
        query = embedding_request("cohere.embed-english-v3", "hello", 1024, "query")
        self.assertEqual(query["input_type"], "search_query")
        document = embedding_request("cohere.embed-english-v3", "hello", 1024, "document")
        self.assertEqual(document["input_type"], "search_document")


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = shared_app_server().port

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30)

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def upload_big(self):
        kb = self.http.post("/api/knowledge-bases", json={"name": "Big", "chunk_size": 100, "chunk_overlap": 0}).json()
        text = " ".join(f"word{i}" for i in range(12000))
        return self.http.post(
            f"/api/knowledge-bases/{kb['id']}/documents", files={"file": ("big.txt", text.encode(), "text/plain")}
        )

    def test_default_chunk_limit_is_generous(self):
        response = self.upload_big()
        self.assertEqual(response.status_code, 201, response.text)
        self.assertGreater(response.json()["chunk_count"], 400)

    def test_chunk_limit_is_configurable(self):
        with mock.patch.dict(os.environ, {"RAGFLOW_MAX_CHUNKS": "50"}):
            response = self.upload_big()
        self.assertEqual(response.status_code, 400)
        self.assertIn("The limit is 50", response.json()["detail"])
        self.assertIn("RAGFLOW_MAX_CHUNKS", response.json()["detail"])

    def test_there_is_no_chunk_limit_by_default(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("RAGFLOW_MAX_CHUNKS", None)
            self.assertEqual(max_chunks(), 0)
            self.assertEqual(self.upload_big().status_code, 201)

    def test_bad_limit_values_mean_no_limit(self):
        for value in ("abc", "-5", "0"):
            with mock.patch.dict(os.environ, {"RAGFLOW_MAX_CHUNKS": value}):
                self.assertEqual(max_chunks(), 0)

    def test_health_and_ui(self):
        health = self.http.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mcp_path"], "/mcp")
        page = self.http.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("RAGFlow", page.text)

    def test_create_sample_and_grounded_answer(self):
        created = self._create("Field notes", nn_algorithm="hnsw")
        sample = self.http.post(f"/api/knowledge-bases/{created['id']}/sample")
        self.assertEqual(sample.status_code, 201, sample.text)
        self.assertGreater(sample.json()["chunk_count"], 0)
        answer = self.http.post(
            f"/api/knowledge-bases/{created['id']}/ask",
            json={"question": "What is the Dublin warehouse code?", "top_k": 3},
        )
        self.assertEqual(answer.status_code, 200, answer.text)
        body = answer.json()
        self.assertEqual(body["mode"], "retrieval")
        self.assertIn("DUB-14", body["answer"])
        self.assertTrue(any("DUB-14" in item["text"] for item in body["citations"]))

    def test_docx_upload_and_exact_search(self):
        created = self._create("Policies", nn_algorithm="exact", distance="euclid")
        document = Document()
        document.add_paragraph("The standby generator room code is GEN-77.")
        payload = _docx_bytes(document)
        uploaded = self.http.post(
            f"/api/knowledge-bases/{created['id']}/documents",
            files={"file": ("policy.docx", payload, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        found = self.http.post(
            f"/api/knowledge-bases/{created['id']}/search",
            json={"question": "standby generator room code"},
        )
        self.assertEqual(found.status_code, 200, found.text)
        self.assertIn("GEN-77", found.json()["citations"][0]["text"])

    def test_rejects_overlap_that_covers_the_whole_chunk(self):
        response = self.http.post(
            "/api/knowledge-bases",
            json={"name": "Bad", "chunk_size": 200, "chunk_overlap": 200},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("chunk_overlap", response.json()["detail"])

    def test_delete_document_removes_it_from_answers(self):
        created = self._create("Temporary")
        self.http.post(f"/api/knowledge-bases/{created['id']}/sample")
        documents = self.http.get(f"/api/knowledge-bases/{created['id']}/documents").json()["documents"]
        removed = self.http.delete(
            f"/api/knowledge-bases/{created['id']}/documents/{documents[0]['id']}"
        )
        self.assertEqual(removed.status_code, 204)
        answer = self.http.post(
            f"/api/knowledge-bases/{created['id']}/ask",
            json={"question": "What is the Dublin warehouse code?"},
        )
        self.assertEqual(answer.json()["mode"], "empty")

    def test_upload_size_limit_is_off_by_default_and_configurable(self):
        created = self._create("Big files")
        url = f"/api/knowledge-bases/{created['id']}/documents"
        body = ("Dublin warehouse code DUB-14. " * 80000).encode()
        self.assertGreater(len(body), 2 * 1024 * 1024)
        os.environ["RAGFLOW_MAX_UPLOAD_MB"] = "1"
        try:
            rejected = self.http.post(url, files={"file": ("big.txt", body)})
        finally:
            del os.environ["RAGFLOW_MAX_UPLOAD_MB"]
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("1 MB", rejected.json()["detail"])
        self.assertEqual(self.http.post(url, files={"file": ("big.txt", body)}).status_code, 201)

    def test_uploads_index_in_parallel_without_blocking_the_server(self):
        import threading
        import time
        from unittest import mock

        from backend import service

        created = self._create("Parallel")
        url = f"/api/knowledge-bases/{created['id']}/documents"
        real_embed = service.embed

        def slow_embed(*args, **kwargs):
            time.sleep(0.6)
            return real_embed(*args, **kwargs)

        statuses, health_times = [], []

        def upload(index):
            statuses.append(self.http.post(url, files={"file": (f"f{index}.txt", f"File {index} about Dublin.".encode())}).status_code)

        with mock.patch.object(service, "embed", slow_embed):
            started = time.monotonic()
            threads = [threading.Thread(target=upload, args=(i,)) for i in range(4)]
            for thread in threads:
                thread.start()
            time.sleep(0.2)
            for _ in range(3):
                before = time.monotonic()
                self.assertEqual(self.http.get("/api/health").status_code, 200)
                health_times.append(time.monotonic() - before)
            for thread in threads:
                thread.join()
            elapsed = time.monotonic() - started
        self.assertEqual(statuses, [201] * 4)
        self.assertLess(max(health_times), 0.3)
        self.assertLess(elapsed, 4 * 0.6)
        self.assertEqual(len(self.http.get(url).json()["documents"]), 4)

    def test_settings_round_trip(self):
        created = self._create("Editable")
        updated = self.http.patch(
            f"/api/knowledge-bases/{created['id']}",
            json={"chunk_size": 500, "chunk_overlap": 40, "nn_algorithm": "exact"},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        body = updated.json()
        self.assertEqual(body["chunk_size"], 500)
        self.assertEqual(body["nn_algorithm"], "exact")
        self.assertEqual(body["embed_model"], created["embed_model"])

    def test_mcp_lists_tools_and_answers(self):
        created = self._create("Agent base")
        self.http.post(f"/api/knowledge-bases/{created['id']}/sample")

        async def run():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(f"http://127.0.0.1:{self.port}/mcp") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    result = await session.call_tool(
                        "ask_knowledge_base",
                        {
                            "knowledge_base": created["name"],
                            "question": "What is the Dublin warehouse code?",
                        },
                    )
                    return listed, result

        listed, result = asyncio.run(run())
        names = {tool.name for tool in listed.tools}
        self.assertIn("ask_knowledge_base", names)
        self.assertIn("search_knowledge_base", names)
        self.assertFalse(getattr(result, "isError", False), result)
        self.assertIn("DUB-14", _tool_text(result))

    def _create(self, name: str, **extra) -> dict:
        payload = {"name": name}
        payload.update(extra)
        response = self.http.post("/api/knowledge-bases", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()


def _docx_bytes(document: Document) -> bytes:
    import io

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _tool_text(result) -> str:
    structured = getattr(result, "structuredContent", None)
    if structured:
        return json.dumps(structured)
    return "\n".join(getattr(block, "text", str(block)) for block in result.content)


if __name__ == "__main__":
    unittest.main()
