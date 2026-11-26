"""Tests for chats saved per knowledge base."""

import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import audit, db
from backend.vectors import close_client
from tests import fake_openai
from tests.helpers import Server, shared_app_server

SECRET = "sk-test-secret-value"


class ChatTests(unittest.TestCase):
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
        fake_openai.judge_replies.clear()
        self.http = httpx.Client(base_url=self.app.url, timeout=30)
        body = {
            "openai": {"base_url": self.fake.url + "/v1", "api_key": SECRET},
            "llm": {"provider": "openai", "model": "fake-chat"},
            "embedding": {"provider": "openai", "model": "fake-embed", "dimensions": 384},
            "judge": {"provider": "none"},
        }
        self.assertEqual(self.http.put("/api/models", json=body).status_code, 200)

    def tearDown(self):
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def kb(self, name="Handbook"):
        kb = self.http.post("/api/knowledge-bases", json={"name": name, "chunk_size": 100, "chunk_overlap": 0}).json()
        self.http.post(f"/api/knowledge-bases/{kb['id']}/sample")
        return kb

    def ask(self, kb, question="What is the Dublin warehouse code?", source="ui", **extra):
        response = self.http.post(f"/api/knowledge-bases/{kb['id']}/ask", json={"question": question, "source": source, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def chats(self, kb):
        return self.http.get(f"/api/knowledge-bases/{kb['id']}/conversations").json()["conversations"]

    def test_an_empty_knowledge_base_still_reports_a_time_so_the_page_shows_no_nan(self):
        kb = self.http.post("/api/knowledge-bases", json={"name": "Empty", "chunk_size": 100, "chunk_overlap": 0}).json()
        answer = self.ask(kb)
        self.assertEqual(answer["mode"], "empty")
        self.assertIsInstance(answer["elapsed_ms"], int)

    def test_the_first_question_starts_a_chat_titled_by_it(self):
        kb = self.kb()
        answer = self.ask(kb, "  What is the   Dublin warehouse code?  ")
        self.assertTrue(answer["conversation_id"])
        listed = self.chats(kb)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], answer["conversation_id"])
        self.assertEqual(listed[0]["title"], "What is the Dublin warehouse code?")
        self.assertEqual(listed[0]["turns"], 1)

    def test_later_questions_join_the_chat_and_come_back_in_order(self):
        kb = self.kb()
        first = self.ask(kb, "First question about Dublin?")
        second = self.ask(kb, "Second question about Dublin?", conversation_id=first["conversation_id"])
        self.assertEqual(second["conversation_id"], first["conversation_id"])
        self.assertEqual(len(self.chats(kb)), 1)
        chat = self.http.get(f"/api/conversations/{first['conversation_id']}").json()
        self.assertEqual([turn["question"] for turn in chat["turns"]], ["First question about Dublin?", "Second question about Dublin?"])
        turn = chat["turns"][0]
        self.assertEqual(turn["result"]["answer"], first["answer"])
        self.assertEqual(turn["result"]["mode"], "llm")
        self.assertEqual(turn["result"]["llm"], {"provider": "openai", "model": "fake-chat"})
        self.assertEqual(turn["result"]["interaction_id"], first["interaction_id"])
        self.assertEqual(len(turn["result"]["citations"]), len(first["citations"]))
        self.assertIsNone(turn["result"]["rerank"])

    def test_ratings_and_scores_are_restored_with_the_chat(self):
        kb = self.kb()
        answer = self.ask(kb)
        self.http.post(f"/api/interactions/{answer['interaction_id']}/feedback", json={"rating": "down", "reason": "incomplete", "comment": "More please"})
        self.http.post(f"/api/interactions/{answer['interaction_id']}/score")
        turn = self.http.get(f"/api/conversations/{answer['conversation_id']}").json()["turns"][0]
        self.assertEqual((turn["interaction"]["rating"], turn["interaction"]["rating_reason"], turn["interaction"]["rating_comment"]), ("down", "incomplete", "More please"))
        self.assertEqual(turn["interaction"]["faithfulness"], 1.0)
        self.assertTrue(turn["interaction"]["judged_at"])
        self.assertFalse(turn["result"]["scoring"])

    def test_an_unscored_recent_answer_is_still_marked_as_scoring(self):
        kb = self.kb()
        answer = self.ask(kb)
        turn = self.http.get(f"/api/conversations/{answer['conversation_id']}").json()["turns"][0]
        self.assertTrue(turn["result"]["scoring"])

    def test_each_knowledge_base_has_its_own_chats(self):
        first, second = self.kb("One"), self.kb("Two")
        self.ask(first)
        self.ask(first, "Another one?")
        self.ask(second)
        self.assertEqual(len(self.chats(first)), 2)
        self.assertEqual(len(self.chats(second)), 1)

    def test_a_chat_cannot_be_used_from_another_knowledge_base(self):
        first, second = self.kb("One"), self.kb("Two")
        chat = self.ask(first)["conversation_id"]
        response = self.http.post(f"/api/knowledge-bases/{second['id']}/ask", json={"question": "Dublin?", "source": "ui", "conversation_id": chat})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.http.post(f"/api/knowledge-bases/{first['id']}/ask", json={"question": "Dublin?", "source": "ui", "conversation_id": "nope"}).status_code, 404)
        self.assertEqual(len(self.chats(second)), 0)

    def test_api_and_mcp_style_asks_do_not_create_chats(self):
        kb = self.kb()
        answer = self.ask(kb, source="api")
        self.assertIsNone(answer["conversation_id"])
        self.assertEqual(self.chats(kb), [])
        self.assertEqual(self.http.get("/api/audit").json()["total"], 1)

    def test_chats_are_listed_newest_activity_first(self):
        kb = self.kb()
        older = self.ask(kb, "Older chat?")["conversation_id"]
        time.sleep(1.1)
        newer = self.ask(kb, "Newer chat?")["conversation_id"]
        self.assertEqual([chat["id"] for chat in self.chats(kb)], [newer, older])
        time.sleep(1.1)
        self.ask(kb, "Back to the older one?", conversation_id=older)
        self.assertEqual([chat["id"] for chat in self.chats(kb)], [older, newer])

    def test_deleting_a_chat_keeps_its_answers_in_the_audit_log(self):
        kb = self.kb()
        answer = self.ask(kb)
        self.assertEqual(self.http.delete(f"/api/conversations/{answer['conversation_id']}").status_code, 204)
        self.assertEqual(self.chats(kb), [])
        self.assertEqual(self.http.get(f"/api/conversations/{answer['conversation_id']}").status_code, 404)
        self.assertEqual(self.http.delete(f"/api/conversations/{answer['conversation_id']}").status_code, 404)
        item = self.http.get(f"/api/interactions/{answer['interaction_id']}").json()
        self.assertEqual(item["question"], "What is the Dublin warehouse code?")
        self.assertIsNone(item["conversation_id"])

    def test_deleting_the_knowledge_base_deletes_its_chats(self):
        kb = self.kb()
        chat = self.ask(kb)["conversation_id"]
        self.assertEqual(self.http.delete(f"/api/knowledge-bases/{kb['id']}").status_code, 204)
        self.assertEqual(self.http.get(f"/api/conversations/{chat}").status_code, 404)
        self.assertEqual(self.http.get(f"/api/knowledge-bases/{kb['id']}/conversations").status_code, 404)

    def test_chats_survive_a_restart_of_the_database_connection_and_old_databases_are_upgraded(self):
        database = Path(self.tmp.name) / "ragflow.db"
        with sqlite3.connect(database) as conn:
            conn.executescript(
                """
                CREATE TABLE interactions (
                    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL, kb_id TEXT NOT NULL,
                    kb_name TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, mode TEXT NOT NULL,
                    top_k INTEGER NOT NULL
                );
                INSERT INTO interactions VALUES ('old', '2026-01-01T00:00:00+00:00', 'ui', 'k', 'K', 'Old?', 'Old.', 'llm', 5);
                """
            )
        with db.connection() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(interactions)")}
        self.assertIn("conversation_id", columns)
        with db.connection() as conn:
            self.assertEqual(conn.execute("SELECT question FROM interactions WHERE id = 'old'").fetchone()[0], "Old?")

    def test_retention_removes_chats_left_empty(self):
        kb = self.kb()
        chat = self.ask(kb)
        with db.connection() as conn:
            conn.execute("UPDATE interactions SET created_at = '2020-01-01T00:00:00+00:00'")
        self.assertEqual(audit.purge(30), 1)
        self.assertEqual(self.chats(kb), [])
        self.assertEqual(self.http.get(f"/api/conversations/{chat['conversation_id']}").status_code, 404)


if __name__ == "__main__":
    unittest.main()
