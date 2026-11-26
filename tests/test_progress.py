"""Tests for live indexing progress."""

import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import progress, service, vectors
from backend.vectors import close_client
from tests.helpers import shared_app_server

# Each paragraph is about one chunk, so this gives well over two batches of 64.
TEXT = "\n\n".join(f"Paragraph {n} talks about warehouse {n} in Dublin and what ships there on every Tuesday of the month." for n in range(400)).encode()


class ProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = shared_app_server()

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        self.http = httpx.Client(base_url=self.app.url, timeout=60)
        self.http.put("/api/advanced", json={"values": {"embed_workers": "1"}})
        self.kb = self.http.post("/api/knowledge-bases", json={"name": "Big", "chunk_size": 100, "chunk_overlap": 0}).json()

    def tearDown(self):
        self.http.put("/api/advanced", json={"clear": ["embed_workers"]})
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def upload(self, upload_id=""):
        headers = {"X-Upload-Id": upload_id} if upload_id else {}
        return self.http.post(
            f"/api/knowledge-bases/{self.kb['id']}/documents", files={"file": ("big.txt", TEXT)}, headers=headers
        )

    def test_chunks_are_embedded_and_stored_a_batch_at_a_time_and_reported(self):
        seen = []
        real_embed, real_upsert = service.embed, vectors.upsert

        def embed(*args, **kwargs):
            seen.append(("before embed", progress.get("job-1")))
            return real_embed(*args, **kwargs)

        def upsert(collection, points):
            seen.append(("before store", progress.get("job-1")))
            return real_upsert(collection, points)

        with mock.patch.object(service, "embed", embed), mock.patch.object(vectors, "upsert", upsert):
            response = self.upload("job-1")
        self.assertEqual(response.status_code, 201, response.text)
        total = response.json()["chunk_count"]
        self.assertGreater(total, 128)
        first_embed, first_store, second_embed = seen[0][1], seen[1][1], seen[2][1]
        self.assertEqual((first_embed["phase"], first_embed["chunks"], first_embed["embedded"], first_embed["stored"]), ("embedding", total, 0, 0))
        self.assertEqual((first_store["embedded"], first_store["stored"]), (64, 0))
        self.assertEqual((second_embed["embedded"], second_embed["stored"]), (64, 64))
        final = self.http.get("/api/uploads/job-1").json()
        self.assertEqual((final["phase"], final["chunks"], final["embedded"], final["stored"]), ("done", total, total, total))
        self.assertEqual(final["filename"], "big.txt")

    def test_unknown_ids_are_404_and_uploads_without_an_id_still_work(self):
        self.assertEqual(self.http.get("/api/uploads/never-started").status_code, 404)
        self.assertEqual(self.upload().status_code, 201)

    def test_a_failure_part_way_removes_the_chunks_already_stored_and_is_reported(self):
        real_embed = service.embed
        calls = []

        def embed(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise service.BadRequest("The embedding model went away.")
            return real_embed(*args, **kwargs)

        with mock.patch.object(service, "embed", embed):
            response = self.upload("job-2")
        self.assertEqual(response.status_code, 400)
        state = self.http.get("/api/uploads/job-2").json()
        self.assertEqual((state["phase"], state["error"]), ("failed", "The embedding model went away."))
        self.assertEqual(state["stored"], 64)
        self.assertEqual(self.http.get(f"/api/knowledge-bases/{self.kb['id']}/documents").json()["documents"], [])
        search = self.http.post(f"/api/knowledge-bases/{self.kb['id']}/search", json={"question": "warehouse Dublin", "top_k": 5})
        self.assertEqual(search.json()["citations"], [])

    def test_a_rejected_file_reports_failed(self):
        self.assertEqual(
            self.http.post(
                f"/api/knowledge-bases/{self.kb['id']}/documents", files={"file": ("empty.txt", b"   ")}, headers={"X-Upload-Id": "job-3"}
            ).status_code,
            400,
        )
        self.assertEqual(self.http.get("/api/uploads/job-3").json()["phase"], "failed")

    def test_batches_are_embedded_in_parallel_up_to_the_limit(self):
        for workers, expected in (("4", 4), ("2", 2)):
            self.http.put("/api/advanced", json={"values": {"embed_workers": workers}})
            running, peak, lock = [0], [0], threading.Lock()
            real_embed = service.embed

            def embed(*args, **kwargs):
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                time.sleep(0.15)
                try:
                    return real_embed(*args, **kwargs)
                finally:
                    with lock:
                        running[0] -= 1

            with mock.patch.object(service, "embed", embed):
                started = time.time()
                response = self.upload(f"par-{workers}")
                took = time.time() - started
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(peak[0], expected, workers)
            final = self.http.get(f"/api/uploads/par-{workers}").json()
            self.assertEqual((final["embedded"], final["stored"]), (final["chunks"], final["chunks"]))
            batches = -(-final["chunks"] // 64)
            self.assertLess(took, batches * 0.15 * 0.8, "no faster than one at a time")

    def test_the_limit_is_shared_by_files_indexing_at_once(self):
        self.http.put("/api/advanced", json={"values": {"embed_workers": "3"}})
        running, peak, lock = [0], [0], threading.Lock()
        real_embed = service.embed

        def embed(*args, **kwargs):
            with lock:
                running[0] += 1
                peak[0] = max(peak[0], running[0])
            time.sleep(0.1)
            try:
                return real_embed(*args, **kwargs)
            finally:
                with lock:
                    running[0] -= 1

        with mock.patch.object(service, "embed", embed):
            threads = [threading.Thread(target=self.upload, args=(f"share-{n}",)) for n in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertLessEqual(peak[0], 3)
        self.assertEqual(peak[0], 3)

    def test_one_failing_batch_stops_the_rest_and_cleans_up_when_running_in_parallel(self):
        self.http.put("/api/advanced", json={"values": {"embed_workers": "4"}})
        real_embed = service.embed
        calls = []

        def embed(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise service.BadRequest("Throttled.")
            return real_embed(*args, **kwargs)

        with mock.patch.object(service, "embed", embed):
            response = self.upload("job-par-fail")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Throttled.", response.text)
        self.assertEqual(self.http.get(f"/api/knowledge-bases/{self.kb['id']}/documents").json()["documents"], [])
        search = self.http.post(f"/api/knowledge-bases/{self.kb['id']}/search", json={"question": "warehouse Dublin", "top_k": 5})
        self.assertEqual(search.json()["citations"], [])

    def test_old_jobs_are_dropped(self):
        for n in range(progress.MAX_ENTRIES + 5):
            progress.start(f"flood-{n}", "x.txt")
        self.assertIsNone(progress.get("flood-0"))
        self.assertIsNotNone(progress.get(f"flood-{progress.MAX_ENTRIES + 4}"))


class BedrockBatchTests(unittest.TestCase):
    """Cohere Embed takes many texts per request; Titan takes one."""

    class Runtime:
        def __init__(self, dim):
            self.dim, self.bodies = dim, []

        def invoke_model(self, modelId, body):
            import io
            import json

            request = json.loads(body)
            self.bodies.append(request)
            if "texts" in request:
                payload = {"embeddings": {"float": [[0.1] * self.dim for _ in request["texts"]]}}
            else:
                payload = {"embedding": [0.1] * self.dim}
            return {"body": io.BytesIO(json.dumps(payload).encode())}

    def run_embed(self, model, count):
        from backend import bedrock

        runtime = self.Runtime(16)
        creds = {"region": "us-east-1"}
        with mock.patch.object(bedrock, "client", return_value=runtime):
            vectors_out = bedrock.embed_texts(creds, model, [f"text {n}" for n in range(count)], 16, "document")
        return runtime.bodies, vectors_out

    def test_cohere_sends_up_to_96_texts_per_request(self):
        bodies, vectors_out = self.run_embed("cohere.embed-english-v3", 200)
        self.assertEqual([len(body["texts"]) for body in bodies], [96, 96, 8])
        self.assertEqual(len(vectors_out), 200)

    def test_titan_still_sends_one_text_per_request(self):
        bodies, vectors_out = self.run_embed("amazon.titan-embed-text-v2:0", 5)
        self.assertEqual(len(bodies), 5)
        self.assertEqual(len(vectors_out), 5)

    def test_index_batch_size_follows_the_model(self):
        self.assertEqual(service._batch_size({"embed_provider": "bedrock", "embed_model": "cohere.embed-english-v3"}), 96)
        self.assertEqual(service._batch_size({"embed_provider": "bedrock", "embed_model": "amazon.titan-embed-text-v2:0"}), 8)
        self.assertEqual(service._batch_size({"embed_provider": "openai", "embed_model": "text-embedding-3-small"}), 64)


if __name__ == "__main__":
    unittest.main()
