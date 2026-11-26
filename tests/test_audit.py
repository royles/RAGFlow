"""Tests for the answer audit log, feedback, and LLM-judged scores."""

import asyncio
import csv
import io
import json
import os
import tempfile
import time
import unittest

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import audit, db
from backend.vectors import close_client
from tests import fake_openai
from tests.helpers import Server, shared_app_server

SECRET = "sk-test-secret-value"


class AuditTests(unittest.TestCase):
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
        os.environ.pop("RAGFLOW_AUDIT_RETENTION_DAYS", None)
        fake_openai.calls.clear()
        fake_openai.judge_replies.clear()
        self.http = httpx.Client(base_url=self.app.url, timeout=30)

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def configure(self, judge="none"):
        body = {
            "openai": {"base_url": self.fake.url + "/v1", "api_key": SECRET},
            "llm": {"provider": "openai", "model": "fake-chat"},
            "embedding": {"provider": "openai", "model": "fake-embed", "dimensions": 384},
            "rerank": {"provider": "none"},
            "judge": {"provider": judge, "model": "fake-judge" if judge == "openai" else ""},
        }
        response = self.http.put("/api/models", json=body)
        self.assertEqual(response.status_code, 200, response.text)

    def kb(self, name="Handbook"):
        kb = self.http.post("/api/knowledge-bases", json={"name": name, "chunk_size": 100, "chunk_overlap": 0}).json()
        self.http.post(f"/api/knowledge-bases/{kb['id']}/sample")
        return kb

    def ask(self, kb, source="ui"):
        response = self.http.post(
            f"/api/knowledge-bases/{kb['id']}/ask",
            json={"question": "What is the Dublin warehouse code?", "source": source},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_for_scores(self, interaction_id):
        for _ in range(100):
            item = self.http.get(f"/api/interactions/{interaction_id}").json()
            if item["judged_at"]:
                return item
            time.sleep(0.05)
        self.fail("The answer was never scored")

    def test_every_answer_is_logged_with_its_context(self):
        self.configure()
        kb = self.kb()
        answer = self.ask(kb)
        item = self.http.get(f"/api/interactions/{answer['interaction_id']}").json()
        self.assertEqual(item["question"], "What is the Dublin warehouse code?")
        self.assertEqual(item["answer"], answer["answer"])
        self.assertEqual(item["source"], "ui")
        self.assertFalse(answer["scoring"])
        self.assertEqual(item["kb_name"], "Handbook")
        self.assertEqual((item["llm_provider"], item["llm_model"]), ("openai", "fake-chat"))
        self.assertEqual((item["chunk_size"], item["chunk_overlap"]), (100, 0))
        self.assertEqual(item["embed_model"], "fake-embed")
        self.assertEqual(len(item["citations"]), len(answer["citations"]))
        self.assertIsNone(item["rating"])
        self.assertIsNone(item["faithfulness"])

    def test_source_defaults_to_api_and_ignores_unknown_values(self):
        self.configure()
        kb = self.kb()
        self.assertEqual(self.http.get(f"/api/interactions/{self.ask(kb, 'api')['interaction_id']}").json()["source"], "api")
        self.assertEqual(self.http.get(f"/api/interactions/{self.ask(kb, 'bogus')['interaction_id']}").json()["source"], "api")

    def test_empty_answers_are_not_logged(self):
        self.configure()
        kb = self.http.post("/api/knowledge-bases", json={"name": "Empty"}).json()
        answer = self.ask(kb)
        self.assertEqual(answer["mode"], "empty")
        self.assertNotIn("interaction_id", answer)
        self.assertEqual(self.http.get("/api/audit").json()["total"], 0)

    def test_feedback_is_append_only_and_the_latest_wins(self):
        self.configure()
        interaction_id = self.ask(self.kb())["interaction_id"]
        url = f"/api/interactions/{interaction_id}/feedback"
        self.assertEqual(self.http.post(url, json={"rating": "up"}).json()["rating"], "up")
        down = self.http.post(url, json={"rating": "down", "reason": "incorrect", "comment": "Wrong code"}).json()
        self.assertEqual((down["rating"], down["rating_reason"], down["rating_comment"]), ("down", "incorrect", "Wrong code"))
        self.assertEqual(len(down["feedback_events"]), 2)
        withdrawn = self.http.post(url, json={"rating": "none"}).json()
        self.assertIsNone(withdrawn["rating"])
        self.assertEqual([e["rating"] for e in withdrawn["feedback_events"]], ["up", "down", "none"])

    def test_a_reason_is_only_kept_with_a_thumbs_down(self):
        self.configure()
        interaction_id = self.ask(self.kb())["interaction_id"]
        item = self.http.post(
            f"/api/interactions/{interaction_id}/feedback", json={"rating": "up", "reason": "incorrect"}
        ).json()
        self.assertEqual(item["rating_reason"], "")

    def test_invalid_feedback_is_rejected(self):
        self.configure()
        interaction_id = self.ask(self.kb())["interaction_id"]
        url = f"/api/interactions/{interaction_id}/feedback"
        for body in ({"rating": "sideways"}, {"rating": "down", "reason": "boring"}, {"rating": "down", "comment": "x" * 1001}):
            self.assertEqual(self.http.post(url, json=body).status_code, 400, body)
        self.assertEqual(self.http.post("/api/interactions/nope/feedback", json={"rating": "up"}).status_code, 404)

    def test_audit_list_filters_and_summarises(self):
        self.configure()
        kb = self.kb()
        first, second, third = (self.ask(kb)["interaction_id"] for _ in range(3))
        self.http.post(f"/api/interactions/{first}/feedback", json={"rating": "up"})
        self.http.post(f"/api/interactions/{second}/feedback", json={"rating": "down"})
        listing = self.http.get("/api/audit").json()
        self.assertEqual(listing["total"], 3)
        self.assertEqual(listing["summary"]["approval"], 0.5)
        self.assertEqual((listing["summary"]["up"], listing["summary"]["down"]), (1, 1))
        ids = lambda **params: {i["id"] for i in self.http.get("/api/audit", params=params).json()["items"]}
        self.assertEqual(ids(rating="down"), {second})
        self.assertEqual(ids(rating="unrated"), {third})
        self.assertEqual(ids(kb_id="other"), set())
        self.assertEqual(self.http.get("/api/audit", params={"rating": "meh"}).status_code, 400)

    def test_judge_scores_answers_in_the_background(self):
        self.configure(judge="openai")
        fake_openai.judge_replies["faithfulness"] = json.dumps(
            {
                "claims": [
                    {"claim": "The code is DUB-14", "supported": True, "source": 1},
                    {"claim": "It opened in 1999", "supported": False, "source": None},
                ]
            }
        )
        fake_openai.judge_replies["relevance"] = "Sure!\n```json\n{\"score\": 4, \"reason\": \"Mostly.\"}\n```"
        answer = self.ask(self.kb())
        self.assertTrue(answer["scoring"])
        item = self.wait_for_scores(answer["interaction_id"])
        self.assertEqual(item["faithfulness"], 0.5)
        self.assertEqual([c["supported"] for c in item["faithfulness_claims"]], [True, False])
        self.assertEqual(item["relevance"], 0.75)
        self.assertEqual(item["relevance_reason"], "Mostly.")
        self.assertEqual(item["judge_model"], "fake-judge")
        self.assertEqual(item["judge_error"], "")
        judge_call = [c for c in fake_openai.calls if c["body"]["model"] == "fake-judge"]
        self.assertEqual(len(judge_call), 2)

    def test_judge_can_reuse_the_llm(self):
        self.configure(judge="same")
        item = self.wait_for_scores(self.ask(self.kb())["interaction_id"])
        self.assertEqual(item["judge_model"], "fake-chat")
        self.assertEqual(item["faithfulness"], 1.0)
        self.assertEqual(item["relevance"], 1.0)

    def test_answers_are_scored_by_default_with_the_llm(self):
        body = {
            "openai": {"base_url": self.fake.url + "/v1", "api_key": SECRET},
            "llm": {"provider": "openai", "model": "fake-chat"},
            "embedding": {"provider": "openai", "model": "fake-embed", "dimensions": 384},
        }
        self.assertEqual(self.http.put("/api/models", json=body).status_code, 200)
        self.assertEqual(self.http.get("/api/models").json()["judge"]["provider"], "same")
        answer = self.ask(self.kb())
        self.assertTrue(answer["scoring"])
        item = self.wait_for_scores(answer["interaction_id"])
        self.assertEqual((item["faithfulness"], item["relevance"]), (1.0, 1.0))

    def test_with_no_judge_nothing_is_scored_until_asked_and_then_the_llm_scores(self):
        self.configure()
        answer = self.ask(self.kb())
        self.assertFalse(answer["scoring"])
        time.sleep(0.2)
        self.assertIsNone(self.http.get(f"/api/interactions/{answer['interaction_id']}").json()["judged_at"])
        scored = self.http.post(f"/api/interactions/{answer['interaction_id']}/score").json()
        self.assertEqual((scored["faithfulness"], scored["judge_model"]), (1.0, "fake-chat"))

    def test_scoring_on_request_needs_an_llm(self):
        self.configure()
        interaction_id = self.ask(self.kb())["interaction_id"]
        self.http.put("/api/models", json={"llm": {"provider": "none"}})
        scored = self.http.post(f"/api/interactions/{interaction_id}/score").json()
        self.assertIsNone(scored["faithfulness"])
        self.assertIn("No LLM", scored["judge_error"])

    def test_unparseable_judge_replies_are_recorded_not_raised(self):
        self.configure(judge="same")
        fake_openai.judge_replies["faithfulness"] = "I cannot do that"
        item = self.wait_for_scores(self.ask(self.kb())["interaction_id"])
        self.assertIsNone(item["faithfulness"])
        self.assertEqual(item["relevance"], 1.0)
        self.assertIn("Faithfulness", item["judge_error"])

    def test_an_answer_with_no_claims_is_fully_faithful(self):
        self.configure(judge="same")
        fake_openai.judge_replies["faithfulness"] = '{"claims": []}'
        item = self.wait_for_scores(self.ask(self.kb())["interaction_id"])
        self.assertEqual(item["faithfulness"], 1.0)

    def test_low_faithfulness_filter(self):
        self.configure(judge="same")
        fake_openai.judge_replies["faithfulness"] = '{"claims": [{"claim": "x", "supported": false}]}'
        interaction_id = self.ask(self.kb())["interaction_id"]
        self.wait_for_scores(interaction_id)
        low = self.http.get("/api/audit", params={"scored": "low"}).json()
        self.assertEqual([i["id"] for i in low["items"]], [interaction_id])
        self.assertEqual(low["summary"]["faithfulness"], 0.0)

    def test_judge_connection_test(self):
        self.configure(judge="openai")
        result = self.http.post("/api/models/test", json={"role": "judge", "settings": {}}).json()
        self.assertTrue(result["ok"], result)
        self.assertIn("valid scores", result["detail"])
        self.configure()
        self.assertTrue(self.http.post("/api/models/test", json={"role": "judge", "settings": {}}).json()["skipped"])

    def test_same_judge_without_an_llm_fails_clearly(self):
        self.http.put("/api/models", json={"judge": {"provider": "same"}})
        result = self.http.post("/api/models/test", json={"role": "judge", "settings": {}}).json()
        self.assertFalse(result["ok"])
        self.assertIn("No LLM", result["detail"])

    def test_the_audit_survives_deleting_the_knowledge_base(self):
        self.configure()
        kb = self.kb()
        interaction_id = self.ask(kb)["interaction_id"]
        self.http.post(f"/api/interactions/{interaction_id}/feedback", json={"rating": "down"})
        self.assertEqual(self.http.delete(f"/api/knowledge-bases/{kb['id']}").status_code, 204)
        item = self.http.get(f"/api/interactions/{interaction_id}").json()
        self.assertEqual(item["kb_name"], "Handbook")
        self.assertEqual(item["rating"], "down")

    def test_export_jsonl_and_csv(self):
        self.configure()
        interaction_id = self.ask(self.kb())["interaction_id"]
        self.http.post(
            f"/api/interactions/{interaction_id}/feedback", json={"rating": "down", "comment": "=HYPERLINK(\"x\")"}
        )
        jsonl = self.http.get("/api/audit/export")
        self.assertIn("attachment", jsonl.headers["content-disposition"])
        record = json.loads(jsonl.text.strip().splitlines()[0])
        self.assertEqual(record["id"], interaction_id)
        self.assertTrue(record["citations"])
        self.assertEqual(record["feedback_events"][0]["rating"], "down")
        rows = list(csv.DictReader(io.StringIO(self.http.get("/api/audit/export", params={"format": "csv"}).text)))
        self.assertEqual(rows[0]["rating"], "down")
        self.assertEqual(rows[0]["rating_comment"], "'=HYPERLINK(\"x\")")
        self.assertEqual(self.http.get("/api/audit/export", params={"format": "xml"}).status_code, 400)

    def test_retention_purges_old_answers_only(self):
        self.configure()
        kb = self.kb()
        old, recent = self.ask(kb)["interaction_id"], self.ask(kb)["interaction_id"]
        with db.connection() as conn:
            conn.execute("UPDATE interactions SET created_at = '2020-01-01T00:00:00+00:00' WHERE id = ?", (old,))
        self.assertEqual(audit.purge(0), 0)
        self.assertEqual(audit.purge(30), 1)
        self.assertEqual({i["id"] for i in self.http.get("/api/audit").json()["items"]}, {recent})

    def test_mcp_answers_carry_an_id_that_can_be_rated(self):
        self.configure()
        kb = self.kb("Agent base")

        async def run():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(f"{self.app.url}/mcp") as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    asked = await session.call_tool(
                        "ask_knowledge_base", {"knowledge_base": kb["name"], "question": "Dublin warehouse code?"}
                    )
                    interaction_id = json.loads(asked.content[0].text)["interaction_id"]
                    rated = await session.call_tool(
                        "rate_answer", {"interaction_id": interaction_id, "rating": "down", "reason": "incomplete"}
                    )
                    bad = await session.call_tool("rate_answer", {"interaction_id": interaction_id, "rating": "meh"})
                    return interaction_id, rated, bad

        interaction_id, rated, bad = asyncio.run(run())
        self.assertFalse(rated.is_error, rated)
        self.assertTrue(bad.is_error)
        item = self.http.get(f"/api/interactions/{interaction_id}").json()
        self.assertEqual((item["source"], item["rating"], item["rating_reason"]), ("mcp", "down", "incomplete"))


if __name__ == "__main__":
    unittest.main()
