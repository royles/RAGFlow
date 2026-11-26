"""Tests for suggested follow-up questions."""

import os
import tempfile
import time
import unittest

import httpx

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import followups
from backend.vectors import close_client
from tests import fake_openai
from tests.helpers import Server, shared_app_server


class ParseTests(unittest.TestCase):
    def test_kinds_are_kept_repeats_dropped_and_each_kind_comes_first(self):
        reply = (
            'Sure:\n[{"question": "A one?", "kind": "deeper"}, {"question": "a ONE?", "kind": "deeper"}, '
            '{"question": "Asked?", "kind": "related"}, {"question": "Two?", "kind": "deeper"}, '
            '{"question": "Which year?", "kind": "clarify"}, {"question": "Three?", "kind": "related"}]'
        )
        self.assertEqual(
            followups.parse(reply, 3, "asked?"),
            [
                {"question": "A one?", "kind": "deeper"},
                {"question": "Which year?", "kind": "clarify"},
                {"question": "Three?", "kind": "related"},
            ],
        )

    def test_unknown_kinds_and_plain_strings_count_as_deeper(self):
        reply = '["First?", {"question": "Second?", "kind": "weird"}]'
        self.assertEqual([i["kind"] for i in followups.parse(reply, 5)], ["deeper", "deeper"])

    def test_plain_lines_are_accepted_and_non_questions_dropped(self):
        reply = "1. First thing?\n- Second thing?\nA statement.\n"
        self.assertEqual([i["question"] for i in followups.parse(reply, 5)], ["First thing?", "Second thing?"])

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
        self.assertEqual(self.http.put("/api/advanced", json={"values": {"followup_count": "3"}}).status_code, 200)

    def tearDown(self):
        self.http.put("/api/advanced", json={"clear": ["followup_count"]})
        self.http.close()
        close_client()
        self.tmp.cleanup()

    def models(self, llm, judge="none"):
        body = {
            "openai": {"base_url": self.fake.url + "/v1", "api_key": "sk-test-secret-value"},
            "llm": llm,
            "embedding": {"provider": "openai", "model": "fake-embed", "dimensions": 384},
            "judge": {"provider": judge, "model": "fake-judge" if judge == "openai" else ""},
        }
        self.assertEqual(self.http.put("/api/models", json=body).status_code, 200)

    def answer(self, top_k=5):
        kb = self.http.post("/api/knowledge-bases", json={"name": "Handbook", "chunk_size": 100, "chunk_overlap": 0}).json()
        self.http.post(f"/api/knowledge-bases/{kb['id']}/sample")
        response = self.http.post(
            f"/api/knowledge-bases/{kb['id']}/ask", json={"question": "What is the Dublin warehouse code?", "source": "ui", "top_k": top_k}
        )
        return response.json()

    def review(self, answer, wait_for="followups"):
        """The saved interaction once the background review has filled in `wait_for`."""
        deadline = time.time() + 10
        while time.time() < deadline:
            item = self.http.get(f"/api/interactions/{answer['interaction_id']}").json()
            if wait_for == "judged_at" and item["judged_at"] or wait_for == "followups" and item["followups"] is not None:
                return item
            time.sleep(0.1)
        self.fail(f"The background review never filled in {wait_for}")

    def suggest(self, answer):
        return self.review(answer)["followups"]

    def llm_calls(self):
        return [c for c in fake_openai.calls if c["path"] == "chat" and "follow-up questions" in c["body"]["messages"][0]["content"]]

    def test_the_answer_says_suggestions_are_coming_and_they_arrive_in_the_background(self):
        answer = self.answer()
        self.assertTrue(answer["suggesting"])
        self.assertNotIn("followups", answer)
        questions = self.suggest(answer)
        self.assertEqual(
            questions,
            [
                {"question": "What else is in the handbook?", "kind": "deeper"},
                {"question": "Which warehouse region do you mean?", "kind": "clarify"},
                {"question": "How does Dublin compare with Cork?", "kind": "related"},
            ],
        )
        prompt = self.llm_calls()[0]["body"]["messages"][0]["content"]
        self.assertIn("What is the Dublin warehouse code?", prompt)
        self.assertIn("up to 3", prompt)
        for kind in ("deeper", "clarify", "related"):
            self.assertIn(f'"{kind}"', prompt)
        self.assertIn("Passages used for the answer:", prompt)

    def test_they_are_made_in_the_same_step_as_the_scores_with_the_judge_model(self):
        self.models({"provider": "openai", "model": "fake-chat"}, judge="openai")
        answer = self.answer()
        self.assertTrue(answer["scoring"] and answer["suggesting"])
        item = self.review(answer, "judged_at")
        self.assertEqual(len(self.review(answer)["followups"]), 3)
        self.assertIsNotNone(item["faithfulness"])
        self.assertEqual(self.llm_calls()[0]["body"]["model"], "fake-judge")
        self.assertEqual(len(self.llm_calls()), 1)

    def test_without_a_judge_the_llm_makes_them_and_nothing_is_scored(self):
        answer = self.answer()
        self.assertFalse(answer["scoring"])
        item = self.review(answer)
        self.assertEqual(self.llm_calls()[0]["body"]["model"], "fake-chat")
        self.assertIsNone(item["judged_at"])

    def test_neighbouring_sections_are_offered_for_related_topics(self):
        answer = self.answer(top_k=1)
        self.suggest(answer)
        prompt = self.llm_calls()[0]["body"]["messages"][0]["content"]
        self.assertIn("Nearby sections:", prompt)
        used = {c["chunk_index"] for c in answer["citations"]}
        nearby = [line for line in prompt.split("Nearby sections:")[1].splitlines() if line.startswith("- ")]
        self.assertTrue(nearby)
        for line in nearby:
            self.assertNotIn(int(line.split("section ")[1].split(":")[0]), used)

    def test_suggestions_are_restored_with_the_chat_without_another_call(self):
        answer = self.answer()
        first = self.suggest(answer)
        chat = self.http.get(f"/api/conversations/{answer['conversation_id']}").json()
        turn = chat["turns"][0]
        self.assertEqual(turn["interaction"]["followups"], first)
        self.assertFalse(turn["result"]["suggesting"])
        self.assertEqual(len(self.llm_calls()), 1)

    def test_the_count_is_an_advanced_setting_and_zero_turns_them_off(self):
        self.assertEqual(self.http.put("/api/advanced", json={"values": {"followup_count": "2"}}).status_code, 200)
        self.assertEqual(len(self.suggest(self.answer())), 2)
        self.assertEqual(self.http.put("/api/advanced", json={"values": {"followup_count": "0"}}).status_code, 200)
        calls = len(self.llm_calls())
        answer = self.answer()
        self.assertFalse(answer["suggesting"])
        time.sleep(0.5)
        self.assertEqual(len(self.llm_calls()), calls)

    def test_answers_without_an_llm_get_none(self):
        self.models({"provider": "none"})
        answer = self.answer()
        self.assertFalse(answer["suggesting"])
        time.sleep(0.5)
        self.assertEqual(self.llm_calls(), [])

    def test_a_failed_attempt_saves_an_empty_list_so_the_page_stops_waiting(self):
        fake_openai.judge_replies["followups"] = "I cannot help with that"
        self.assertEqual(self.suggest(self.answer()), [])


if __name__ == "__main__":
    unittest.main()
