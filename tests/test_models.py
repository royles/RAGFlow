"""Tests for the model settings API, connection tests, and OpenAI/Bedrock paths."""

import io
import json
import os
import sqlite3
import stat
import tempfile
import unittest
from unittest import mock

import boto3
import httpx
from botocore.response import StreamingBody
from botocore.stub import Stubber

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import bedrock, model_settings, model_test
from backend.vectors import close_client
from tests import fake_openai
from tests.helpers import Server, shared_app_server

SECRET = "sk-test-secret-value"


class ModelApiTests(unittest.TestCase):
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
        for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            os.environ.pop(name, None)
        fake_openai.calls.clear()
        self.http = httpx.Client(base_url=self.app.url, timeout=30)

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def openai_settings(self, **overrides):
        body = {
            "openai": {"base_url": self.fake.url + "/v1", "api_key": SECRET},
            "llm": {"provider": "openai", "model": "fake-chat"},
            "embedding": {"provider": "openai", "model": "fake-embed", "dimensions": 384},
            "rerank": {"provider": "openai", "model": "fake-rerank"},
        }
        body.update(overrides)
        return body

    def save(self, body):
        response = self.http.put("/api/models", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_defaults_need_no_credentials(self):
        body = self.http.get("/api/models").json()
        self.assertEqual(body["llm"]["provider"], "none")
        self.assertEqual(body["embedding"]["provider"], "local")
        self.assertEqual(body["rerank"]["provider"], "none")
        self.assertFalse(body["openai"]["api_key_set"])

    def test_secrets_are_never_returned_and_the_file_is_private(self):
        saved = self.save(
            {
                **self.openai_settings(),
                "bedrock": {"region": "eu-west-1", "access_key_id": "AKIATESTKEY", "secret_access_key": "aws-secret"},
            }
        )
        again = self.http.get("/api/models").text
        for text in (json.dumps(saved), again):
            self.assertNotIn(SECRET, text)
            self.assertNotIn("aws-secret", text)
            self.assertNotIn("AKIATESTKEY", text)
        self.assertTrue(saved["openai"]["api_key_set"])
        self.assertTrue(saved["bedrock"]["secret_access_key_set"])
        self.assertEqual(saved["bedrock"]["credential_source"], "saved")
        mode = stat.S_IMODE(os.stat(os.path.join(self.tmp.name, "model_settings.json")).st_mode)
        self.assertEqual(mode, 0o600)

    def test_blank_secret_keeps_the_value_and_clear_removes_it(self):
        self.save(self.openai_settings())
        kept = self.save({"openai": {"base_url": self.fake.url + "/v1", "api_key": ""}})
        self.assertTrue(kept["openai"]["api_key_set"])
        cleared = self.save({"clear": ["openai.api_key"]})
        self.assertFalse(cleared["openai"]["api_key_set"])
        self.assertEqual(cleared["openai"]["api_key_source"], "none")

    def test_environment_key_is_reported(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "from-env"}):
            body = self.http.get("/api/models").json()
        self.assertEqual(body["openai"]["api_key_source"], "environment")
        self.assertFalse(body["openai"]["api_key_set"])

    def test_invalid_settings_are_rejected(self):
        cases = [
            {"bedrock": {"access_key_id": "AKIA"}},
            {"bedrock": {"region": "Not A Region"}},
            {"openai": {"base_url": "localhost:1234"}},
            {"rerank": {"provider": "bedrock", "model": "amazon.rerank-v1:0", "region": "US West"}},
            {"llm": {"provider": "bedrock", "model": ""}},
            {"llm": {"provider": "magic", "model": "x"}},
            {"embedding": {"provider": "openai", "model": "m", "dimensions": 2}},
            {"embedding": {"provider": "none", "model": "m", "dimensions": 384}},
        ]
        for body in cases:
            response = self.http.put("/api/models", json=body)
            self.assertEqual(response.status_code, 400, body)
        self.assertEqual(self.http.get("/api/models").json()["llm"]["provider"], "none")

    def test_connection_tests_for_every_role(self):
        self.save(self.openai_settings())
        results = {}
        for role in ("llm", "embedding", "rerank"):
            response = self.http.post("/api/models/test", json={"role": role, "settings": {}})
            self.assertEqual(response.status_code, 200)
            results[role] = response.json()
            self.assertTrue(results[role]["ok"], results[role])
        self.assertIn("ready", results["llm"]["detail"])
        self.assertIn("384", results["embedding"]["detail"])
        self.assertIn("first", results["rerank"]["detail"])
        self.assertEqual({call["path"] for call in fake_openai.calls}, {"chat", "embeddings", "rerank"})
        for call in fake_openai.calls:
            self.assertEqual(call["auth"], f"Bearer {SECRET}")

    def test_unsaved_draft_is_tested_and_failures_hide_secrets(self):
        draft = {"openai": {"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-draft-secret"}}
        result = self.http.post("/api/models/test", json={"role": "llm", "settings": {**draft, "llm": {"provider": "openai", "model": "m"}}}).json()
        self.assertFalse(result["ok"])
        self.assertNotIn("sk-draft-secret", json.dumps(result))
        self.assertEqual(self.http.get("/api/models").json()["openai"]["api_key_set"], False)

    def test_dimension_mismatch_is_reported(self):
        self.save(self.openai_settings())
        draft = {"embedding": {"provider": "openai", "model": "fake-embed", "dimensions": 512}}
        result = self.http.post("/api/models/test", json={"role": "embedding", "settings": draft}).json()
        self.assertFalse(result["ok"])
        self.assertIn("384", result["detail"])

    def test_local_and_none_roles_are_skipped(self):
        for role in ("llm", "embedding", "rerank"):
            result = self.http.post("/api/models/test", json={"role": role, "settings": {}}).json()
            self.assertTrue(result["ok"])
            self.assertTrue(result["skipped"])
        self.assertEqual(self.http.post("/api/models/test", json={"role": "nope"}).status_code, 400)

    def test_ask_uses_the_llm_and_reranks(self):
        self.save(self.openai_settings())
        kb = self.http.post("/api/knowledge-bases", json={"name": "Handbook", "chunk_size": 100, "chunk_overlap": 0}).json()
        self.assertEqual(kb["embed_provider"], "openai")
        self.http.post(f"/api/knowledge-bases/{kb['id']}/sample")
        fake_openai.calls.clear()
        answer = self.http.post(
            f"/api/knowledge-bases/{kb['id']}/ask", json={"question": "What is the Dublin warehouse code?", "top_k": 3}
        ).json()
        self.assertEqual(answer["mode"], "llm")
        self.assertEqual(answer["llm"], {"provider": "openai", "model": "fake-chat"})
        self.assertIn("fake model", answer["answer"])
        scores = [item["rerank_score"] for item in answer["citations"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertIn("Dublin", answer["citations"][0]["text"])
        paths = [call["path"] for call in fake_openai.calls]
        self.assertEqual(paths, ["embeddings", "rerank", "chat"])
        rerank_call = fake_openai.calls[1]["body"]
        self.assertGreater(len(rerank_call["documents"]), 3)
        self.assertIn("Dublin", fake_openai.calls[2]["body"]["messages"][0]["content"])

    def test_knowledge_base_keeps_the_embedding_it_was_created_with(self):
        kb = self.http.post("/api/knowledge-bases", json={"name": "Local"}).json()
        self.save(self.openai_settings())
        fake_openai.calls.clear()
        self.assertEqual(self.http.post(f"/api/knowledge-bases/{kb['id']}/sample").status_code, 201)
        self.assertEqual(kb["embed_provider"], "local")
        self.assertNotIn("embeddings", [call["path"] for call in fake_openai.calls])

    def test_each_role_can_use_its_own_base_url(self):
        extra = [Server(fake_openai.app).start() for _ in range(3)]
        self.addCleanup(lambda: [server.stop() for server in extra])
        urls = {role: server.url + "/v1" for role, server in zip(("llm", "embedding", "rerank"), extra)}
        body = self.openai_settings(openai={"base_url": "http://127.0.0.1:1/v1", "api_key": SECRET})
        for role, url in urls.items():
            body[role]["base_url"] = url
        saved = self.save(body)
        for role, url in urls.items():
            self.assertEqual(saved[role]["base_url"], url)
            self.assertEqual(saved[role]["effective_base_url"], url)
        fake_openai.calls.clear()
        for role in urls:
            result = self.http.post("/api/models/test", json={"role": role, "settings": {}}).json()
            self.assertTrue(result["ok"], result)
        hosts = {call["path"]: call["host"] for call in fake_openai.calls}
        self.assertEqual(
            hosts,
            {
                "chat": f"127.0.0.1:{extra[0].port}",
                "embeddings": f"127.0.0.1:{extra[1].port}",
                "rerank": f"127.0.0.1:{extra[2].port}",
            },
        )

    def test_a_role_without_its_own_base_url_uses_the_default(self):
        other = Server(fake_openai.app).start()
        self.addCleanup(other.stop)
        body = self.openai_settings()
        body["llm"]["base_url"] = other.url + "/v1"
        saved = self.save(body)
        self.assertEqual(saved["embedding"]["base_url"], "")
        self.assertEqual(saved["embedding"]["effective_base_url"], self.fake.url + "/v1")
        fake_openai.calls.clear()
        for role in ("llm", "embedding"):
            self.assertTrue(self.http.post("/api/models/test", json={"role": role, "settings": {}}).json()["ok"])
        hosts = {call["path"]: call["host"] for call in fake_openai.calls}
        self.assertEqual(hosts["chat"], f"127.0.0.1:{other.port}")
        self.assertEqual(hosts["embeddings"], f"127.0.0.1:{self.fake.port}")

    def test_role_base_url_must_be_http(self):
        for role in ("llm", "embedding", "rerank"):
            body = self.openai_settings()
            body[role]["base_url"] = "localhost:9000"
            self.assertEqual(self.http.put("/api/models", json=body).status_code, 400, role)

    def test_unsaved_role_base_url_is_tested(self):
        self.save(self.openai_settings(openai={"base_url": "http://127.0.0.1:1/v1", "api_key": SECRET}))
        draft = {"llm": {"provider": "openai", "model": "fake-chat", "base_url": self.fake.url + "/v1"}}
        result = self.http.post("/api/models/test", json={"role": "llm", "settings": draft}).json()
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.http.get("/api/models").json()["llm"]["base_url"], "")

    def test_old_chat_columns_are_dropped(self):
        close_client()
        path = os.path.join(self.tmp.name, "ragflow.db")
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE knowledge_bases (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                  chunk_size INTEGER NOT NULL, chunk_overlap INTEGER NOT NULL,
                  nn_algorithm TEXT NOT NULL, distance TEXT NOT NULL,
                  embed_provider TEXT NOT NULL, embed_model TEXT NOT NULL, embed_dim INTEGER NOT NULL,
                  chat_provider TEXT NOT NULL, chat_model TEXT NOT NULL, bedrock_region TEXT NOT NULL,
                  collection TEXT NOT NULL, created_at TEXT NOT NULL
                );
                INSERT INTO knowledge_bases VALUES
                  ('old', 'Old', '', 800, 120, 'hnsw', 'cosine', 'local', 'local-hashing', 384,
                   'none', '', 'us-east-1', 'kbold', '2026-01-01T00:00:00+00:00');
                """
            )
        rows = self.http.get("/api/knowledge-bases").json()["knowledge_bases"]
        self.assertEqual([row["name"] for row in rows], ["Old"])
        self.assertNotIn("chat_provider", rows[0])
        self.assertNotIn("bedrock_region", rows[0])


class BedrockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        model_settings.save_update(
            {
                "bedrock": {"region": "us-east-1"},
                "llm": {"provider": "bedrock", "model": "anthropic.claude-test"},
                "embedding": {"provider": "bedrock", "model": "amazon.titan-embed-text-v2:0", "dimensions": 1024},
                "rerank": {"provider": "bedrock", "model": "amazon.rerank-v1:0"},
            }
        )
        self.clients = {}
        for service in ("bedrock-runtime", "bedrock-agent-runtime"):
            real = boto3.client(service, region_name="us-east-1", aws_access_key_id="k", aws_secret_access_key="s")
            stub = Stubber(real)
            self.clients[service] = (real, stub)
        self.regions = []

        def fake_client(service, creds):
            self.regions.append((service, creds["region"]))
            return self.clients[service][0]

        patcher = mock.patch("backend.bedrock.client", side_effect=fake_client)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def stub(self, service):
        return self.clients[service][1]

    def run_role(self, role):
        for _, stub in self.clients.values():
            stub.activate()
        result = model_test.run(role, {})
        for _, stub in self.clients.values():
            stub.assert_no_pending_responses()
        return result

    def test_llm(self):
        self.stub("bedrock-runtime").add_response(
            "converse",
            {
                "output": {"message": {"role": "assistant", "content": [{"text": "ready"}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            },
            {
                "modelId": "anthropic.claude-test",
                "messages": [{"role": "user", "content": [{"text": "Reply with the single word: ready"}]}],
                "inferenceConfig": {"maxTokens": 800, "temperature": 0.2},
            },
        )
        result = self.run_role("llm")
        self.assertTrue(result["ok"], result)
        self.assertIn("ready", result["detail"])

    def test_embedding(self):
        payload = json.dumps({"embedding": [0.1] * 1024}).encode()
        self.stub("bedrock-runtime").add_response(
            "invoke_model",
            {"body": StreamingBody(io.BytesIO(payload), len(payload)), "contentType": "application/json"},
            {
                "modelId": "amazon.titan-embed-text-v2:0",
                "body": json.dumps({"inputText": "RAGFlow connection test", "dimensions": 1024, "normalize": True}),
            },
        )
        result = self.run_role("embedding")
        self.assertTrue(result["ok"], result)
        self.assertIn("1024", result["detail"])

    def test_rerank_builds_the_model_arn(self):
        self.stub("bedrock-agent-runtime").add_response(
            "rerank",
            {"results": [{"index": 1, "relevanceScore": 0.9}, {"index": 0, "relevanceScore": 0.1}]},
            {
                "queries": [{"type": "TEXT", "textQuery": {"text": model_test._QUERY}}],
                "sources": [
                    {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": text}}}
                    for text in model_test._DOCUMENTS
                ],
                "rerankingConfiguration": {
                    "type": "BEDROCK_RERANKING_MODEL",
                    "bedrockRerankingConfiguration": {
                        "modelConfiguration": {"modelArn": "arn:aws:bedrock:us-east-1::foundation-model/amazon.rerank-v1:0"},
                        "numberOfResults": 2,
                    },
                },
            },
        )
        result = self.run_role("rerank")
        self.assertTrue(result["ok"], result)

    def test_rerank_can_use_its_own_region(self):
        model_settings.save_update({"rerank": {"provider": "bedrock", "model": "amazon.rerank-v1:0", "region": "us-west-2"}})
        self.stub("bedrock-agent-runtime").add_response(
            "rerank",
            {"results": [{"index": 1, "relevanceScore": 0.9}, {"index": 0, "relevanceScore": 0.1}]},
            {
                "queries": [{"type": "TEXT", "textQuery": {"text": model_test._QUERY}}],
                "sources": [
                    {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": text}}}
                    for text in model_test._DOCUMENTS
                ],
                "rerankingConfiguration": {
                    "type": "BEDROCK_RERANKING_MODEL",
                    "bedrockRerankingConfiguration": {
                        "modelConfiguration": {"modelArn": "arn:aws:bedrock:us-west-2::foundation-model/amazon.rerank-v1:0"},
                        "numberOfResults": 2,
                    },
                },
            },
        )
        self.assertTrue(self.run_role("rerank")["ok"])
        self.assertEqual(self.regions, [("bedrock-agent-runtime", "us-west-2")])
        public = model_settings.public(model_settings.load())
        self.assertEqual(public["rerank"]["effective_region"], "us-west-2")
        self.assertEqual(public["llm"]["effective_region"], "us-east-1")

    def test_invalid_model_error_explains_the_region(self):
        self.stub("bedrock-agent-runtime").add_client_error(
            "rerank", "ValidationException", "The provided model identifier is invalid."
        )
        result = self.run_role("rerank")
        self.assertFalse(result["ok"])
        self.assertIn("The region used was us-east-1", result["detail"])
        self.assertIn("us-west-2", result["detail"])

    def test_on_demand_error_suggests_an_inference_profile(self):
        self.stub("bedrock-runtime").add_client_error(
            "converse",
            "ValidationException",
            "Invocation of model ID anthropic.claude-test with on-demand throughput isn\u2019t supported. "
            "Retry your request with the ID or ARN of an inference profile that contains this model.",
        )
        detail = self.run_role("llm")["detail"]
        self.assertIn("us.anthropic.claude-test", detail)
        self.assertIn("global.anthropic.claude-test", detail)

    def test_inference_profile_hint_does_not_double_the_prefix(self):
        error = bedrock.ClientError({"Error": {"Code": "ValidationException", "Message": "use an inference profile"}}, "Converse")
        hint = bedrock._profile_hint(error, "us.anthropic.claude-test")
        self.assertIn("eu.anthropic.claude-test", hint)
        self.assertNotIn("us.us.", hint)
        self.assertEqual(bedrock._profile_hint(error, "arn:aws:bedrock:us-east-1:1:inference-profile/x"), "")

    def test_other_errors_get_no_region_hint(self):
        self.stub("bedrock-agent-runtime").add_client_error("rerank", "AccessDeniedException", "No access")
        self.assertNotIn("region used", self.run_role("rerank")["detail"])

    def test_aws_errors_are_a_failed_test_not_a_crash(self):
        self.stub("bedrock-runtime").add_client_error("converse", "AccessDeniedException", "You do not have access")
        result = self.run_role("llm")
        self.assertFalse(result["ok"])
        self.assertIn("rejected the chat request", result["detail"])

    def test_saved_keys_are_passed_to_boto3(self):
        creds = {"region": "eu-west-1", "access_key_id": "AKIATEST", "secret_access_key": "s", "session_token": None}
        with mock.patch("backend.bedrock.boto3.client") as make:
            mock.patch.stopall()
            bedrock.client("bedrock-runtime", creds)
        make.assert_called_once_with(
            "bedrock-runtime",
            region_name="eu-west-1",
            aws_access_key_id="AKIATEST",
            aws_secret_access_key="s",
            aws_session_token=None,
        )


if __name__ == "__main__":
    unittest.main()
