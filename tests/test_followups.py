"""Tests for suggested follow-up questions."""

import os
import tempfile
import unittest

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import followups
from backend.vectors import close_client
from tests import fake_openai
from tests.helpers import Server, shared_app_server


class ParseTests(unittest.TestCase):
    def test_json_list_is_trimmed_to_the_count_and_drops_repeats(self):
        reply = 'Sure:\n["A one?", "a ONE?", "Asked?", "Two?", "Three?", "Four?"]'
        self.assertEqual(followups.parse(reply, 3, "asked?"), ["A one?", "Two?", "Three?"])

    def test_plain_lines_are_accepted_and_non_questions_dropped(self):
        reply = "1. First thing?\n- Second thing?\nA statement.\n"
        self.assertEqual(followups.parse(reply, 5), ["First thing?", "Second thing?"])

    def test_junk_gives_nothing(self):
        self.assertEqual(followups.parse("no idea", 3), [])


class FollowupTests(unittest.TestCase):
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
        fake_openai.calls.clear()
        self.http = httpx.Client(base_url=self.app.url, timeout=30)
        self.models({"provider": "openai", "model": "fake-chat"})

    def tearDown(self):
        self.http.put("/api/advanced", json={"clear": ["followup_count"]})
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def models(self, llm):
        body = {
            "openai": {"base_url": self.fake.url + "/v1", "api_key": "sk-test-secret-value"},
            "llm": llm,
            "embedding": {"provider": "openai", "model": "fake-embed", "dimensions": 384},
            "judge": {"provider": "none"},
        }
        self.assertEqual(self.http.put("/api/models", json=body).status_code, 200)

    def answer(self):
        kb = self.http.post("/api/knowledge-bases", json={"name": "Handbook", "chunk_size": 100, "chunk_overlap": 0}).json()
        self.http.post(f"/api/knowledge-bases/{kb['id']}/sample")
        response = self.http.post(
            f"/api/knowledge-bases/{kb['id']}/ask", json={"question": "What is the Dublin warehouse code?", "source": "ui"}
        )
        return response.json()

    def suggest(self, answer):
        response = self.http.post(f"/api/interactions/{answer['interaction_id']}/followups")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["questions"]

    def llm_calls(self):
        return [c for c in fake_openai.calls if c["path"] == "chat" and "follow-up questions" in c["body"]["messages"][0]["content"]]

    def test_three_questions_by_default_and_they_do_not_slow_the_answer(self):
        answer = self.answer()
        self.assertNotIn("followups", answer)
        self.assertEqual(self.llm_calls(), [])
        questions = self.suggest(answer)
        self.assertEqual(questions, ["What else is in the handbook?", "Who owns the Dublin warehouse?", "When does it open?"])
        prompt = self.llm_calls()[0]["body"]["messages"][0]["content"]
        self.assertIn("What is the Dublin warehouse code?", prompt)
        self.assertIn("Suggest 3", prompt)

    def test_suggestions_are_saved_and_restored_without_another_call(self):
        answer = self.answer()
        first = self.suggest(answer)
        self.assertEqual(self.suggest(answer), first)
        self.assertEqual(len(self.llm_calls()), 1)
        chat = self.http.get(f"/api/conversations/{answer['conversation_id']}").json()
        self.assertEqual(chat["turns"][0]["interaction"]["followups"], first)

    def test_the_count_is_an_advanced_setting_and_zero_turns_them_off(self):
        self.assertEqual(self.http.put("/api/advanced", json={"values": {"followup_count": "2"}}).status_code, 200)
        self.assertEqual(len(self.suggest(self.answer())), 2)
        self.assertEqual(self.http.put("/api/advanced", json={"values": {"followup_count": "0"}}).status_code, 200)
        calls = len(self.llm_calls())
        self.assertEqual(self.suggest(self.answer()), [])
        self.assertEqual(len(self.llm_calls()), calls)

    def test_answers_without_an_llm_get_none(self):
        self.models({"provider": "none"})
        self.assertEqual(self.suggest(self.answer()), [])
        self.assertEqual(self.llm_calls(), [])

    def test_a_failed_attempt_is_not_saved_so_it_can_be_retried(self):
        answer = self.answer()
        self.models({"provider": "openai", "model": "fake-chat"})
        fake_openai.judge_replies["followups"] = "I cannot help with that"
        self.assertEqual(self.suggest(answer), [])
        fake_openai.judge_replies.pop("followups")
        self.assertEqual(len(self.suggest(answer)), 3)


if __name__ == "__main__":
    unittest.main()
