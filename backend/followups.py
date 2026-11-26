"""Suggested follow-up questions for an answer.

Made by the LLM after the answer is shown, so they never delay it, and saved
with the answer so a restored chat shows the same suggestions without another call.
"""

import json
import re

from backend import advanced, audit, model_settings, models

MAX_QUESTION_CHARS = 200
MAX_PASSAGE_CHARS = 600
MAX_PROMPT_CHARS = 6000

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")


def suggest(interaction_id: str) -> list[str]:
    """Saved suggestions, or new ones. Answers not written by an LLM get none."""
    item = audit.get(interaction_id)
    if item["followups"] is not None:
        return item["followups"]
    count = advanced.get("followup_count")
    if count == 0 or item["mode"] != "llm":
        return []
    cfg = model_settings.load()
    reply = models.generate(cfg, _prompt(item, count))
    questions = parse(reply, count, item["question"])
    if questions:
        audit.save_followups(interaction_id, questions)
    return questions


def parse(reply: str, count: int, asked: str = "") -> list[str]:
    """Questions from a model reply: a JSON list, or one question per line."""
    candidates: list = []
    start, end = reply.find("["), reply.rfind("]")
    if 0 <= start < end:
        try:
            data = json.loads(reply[start : end + 1])
            candidates = data if isinstance(data, list) else []
        except ValueError:
            candidates = []
    if not candidates:
        candidates = [_BULLET.sub("", line) for line in reply.splitlines()]
    seen, questions = {asked.strip().lower()}, []
    for candidate in candidates:
        text = str(candidate).strip().strip('"').strip()
        if not text.endswith("?") or text.lower() in seen:
            continue
        seen.add(text.lower())
        questions.append(text[:MAX_QUESTION_CHARS])
        if len(questions) == count:
            break
    return questions


def _prompt(item: dict, count: int) -> str:
    passages, used = [], 0
    for index, citation in enumerate(item["citations"], start=1):
        text = citation["text"][: min(MAX_PASSAGE_CHARS, max(0, MAX_PROMPT_CHARS - used))]
        used += len(text)
        passages.append(f"[{index}] {text}")
    return "\n".join(
        [
            f"Suggest {count} short follow-up questions the user might ask next.",
            "Each must be answerable from the passages below, differ from the question already asked, and make sense on its own.",
            "Reply with only a JSON array of strings.",
            "",
            "Passages:",
            *passages,
            "",
            f"Question asked: {item['question']}",
            f"Answer given: {item['answer'][:1500]}",
        ]
    )
