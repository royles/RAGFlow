"""Tests for chunking, Bedrock request bodies, the HTTP API, and MCP."""

import asyncio
import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

import httpx
import uvicorn
from docx import Document

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend.bedrock import embedding_request
from backend.chunking import chunk_text
from backend.embeddings import local_embed
from backend.main import app
from backend.vectors import close_client


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


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
        cls.port = _free_port()
        cls.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="warning")
        )
        cls.thread = threading.Thread(target=cls.server.run, daemon=True)
        cls.thread.start()
        deadline = 0
        while not cls.server.started and deadline < 50:
            deadline += 1
            cls.thread.join(0.1)
        if not cls.server.started:
            raise RuntimeError("RAGFlow did not start")

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(5)
        close_client()

    def setUp(self):
        close_client()
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        self.http = httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30)

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

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

    def test_settings_round_trip(self):
        created = self._create("Editable")
        updated = self.http.patch(
            f"/api/knowledge-bases/{created['id']}",
            json={"chunk_size": 500, "chunk_overlap": 40, "nn_algorithm": "exact", "chat_preset": "none"},
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
        payload = {"name": name, "embed_preset": "local", "chat_preset": "none"}
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
