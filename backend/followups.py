"""Suggested follow-up questions for an answer.

Three kinds, all written in the background judge step, after the answer is shown,
so they never delay it, and grounded in the retrieved passages:

- deeper: the next detail the user probably wants, from the passages.
- clarify: a sharper version of a vague question, or one that fills a gap when
  the passages are incomplete or out of date (date, region, version, role).
- related: an adjacent topic, comparison or next step from the sections next to
  the retrieved ones.

They are saved with the answer, so a restored chat shows the same suggestions
without another call.
"""

import json
import logging
import re

from backend import advanced, audit, db, model_settings, models, vectors

log = logging.getLogger("ragflow")

KINDS = ("deeper", "clarify", "related")
MAX_QUESTION_CHARS = 200
MAX_PASSAGE_CHARS = 600
MAX_NEARBY = 4
MAX_NEARBY_CHARS = 400
MAX_PROMPT_CHARS = 7000

_BULLET = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")


def enabled() -> bool:
    return advanced.get("followup_count") > 0


def generate(interaction_id: str) -> list[dict]:
    """Make and save suggestions for an answer. Called from the judge step.

    Uses the judge model when one is set, otherwise the LLM. An empty list is
    saved when none could be made, so the page stops waiting for them.
    """
    item = audit.get(interaction_id)
    if item["followups"] is not None or item["mode"] != "llm" or not enabled():
        return item["followups"] or []
    cfg = model_settings.load()
    try:
        reply = models.generate(cfg, _prompt(item, advanced.get("followup_count"), _nearby(item)), "judge" if judge_set(cfg) else "llm")
        questions = parse(reply, advanced.get("followup_count"), item["question"])
    except Exception:
        log.exception("Could not suggest follow-ups for %s", interaction_id)
        questions = []
    audit.save_followups(interaction_id, questions)
    return questions


def judge_set(cfg: dict) -> bool:
    return cfg["judge"]["provider"] != "none"


def parse(reply: str, count: int, asked: str = "") -> list[dict]:
    """Suggestions from a model reply: a JSON list of {question, kind}, or one question per line."""
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
    seen, found = {asked.strip().lower()}, []
    for candidate in candidates:
        if isinstance(candidate, dict):
            text, kind = str(candidate.get("question") or ""), str(candidate.get("kind") or "").lower()
        else:
            text, kind = str(candidate), ""
        text = text.strip().strip('"').strip()
        if not text.endswith("?") or text.lower() in seen:
            continue
        seen.add(text.lower())
        found.append({"question": text[:MAX_QUESTION_CHARS], "kind": kind if kind in KINDS else "deeper"})
    return _spread(found, count)


def _spread(found: list[dict], count: int) -> list[dict]:
    """Keep the first of each kind before a second of any, so the set stays varied."""
    chosen, rest = [], []
    for item in found:
        if item["kind"] in {c["kind"] for c in chosen}:
            rest.append(item)
        else:
            chosen.append(item)
    return sorted((chosen + rest)[:count], key=lambda item: KINDS.index(item["kind"]))


def _nearby(item: dict) -> list[dict]:
    """The chunks just before and after each retrieved one, which often hold the next topic."""
    have = {(c.get("document_id"), c["chunk_index"]) for c in item["citations"]}
    wanted: dict[str, set[int]] = {}
    for citation in item["citations"]:
        document = citation.get("document_id")
        for index in (citation["chunk_index"] - 1, citation["chunk_index"] + 1):
            if document and index >= 0 and (document, index) not in have:
                wanted.setdefault(document, set()).add(index)
    if not wanted:
        return []
    try:
        kb = db.get_kb(item["kb_id"])
        found = vectors.neighbours(kb["collection"], {key: sorted(value) for key, value in wanted.items()})
    except Exception:
        log.exception("Could not read neighbouring chunks")
        return []
    return found[:MAX_NEARBY]


def _prompt(item: dict, count: int, nearby: list[dict]) -> str:
    passages, used = [], 0
    for index, citation in enumerate(item["citations"], start=1):
        text = citation["text"][: min(MAX_PASSAGE_CHARS, max(0, MAX_PROMPT_CHARS - used))]
        used += len(text)
        passages.append(f"[{index}] {citation['filename']}: {text}")
    sections = [f"- {c['filename']}, section {c['chunk_index']}: {c['text'][:MAX_NEARBY_CHARS]}" for c in nearby]
    return "\n".join(
        [
            f"Suggest up to {count} short follow-up questions the user might ask next.",
            "Each must be a question the user could send as-is, make sense on its own, differ from the question already asked, and be answerable from the passages or the nearby sections. Never invent facts or topics that are not in them.",
            "Give each a kind, using as many different kinds as fit:",
            '- "deeper": the next detail the user probably wants, based on the passages and what they seem to be after.',
            '- "clarify": only when the question is vague, or the passages are incomplete, outdated or conflicting. A sharper version of the question that names a missing constraint such as a date, region, product version or user role. Leave it out when the question was already specific and the answer complete.',
            '- "related": an adjacent topic, comparison or next step mentioned in the nearby sections or sources. Leave it out when there is nothing nearby.',
            'Reply with only a JSON array like [{"question": "...", "kind": "deeper"}].',
            "",
            "Passages used for the answer:",
            *passages,
            "",
            "Nearby sections:" if sections else "No nearby sections.",
            *sections,
            "",
            f"Question asked: {item['question']}",
            f"Answer given: {item['answer'][:1500]}",
        ]
    )
