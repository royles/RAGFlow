"""Saved chats, one list per knowledge base.

A conversation is a title plus the answers given in it. The answers themselves
live in the audit log, so a chat shows the same sources, ratings and scores as
the Quality page. Deleting a chat removes it from the chat list only; the
audit log keeps the answers. Deleting a knowledge base deletes its chats.
"""

import time
import uuid
from datetime import datetime, timezone

from backend import audit, db, followups
from backend.errors import NotFound

TITLE_LENGTH = 80
LIST_LIMIT = 100
SCORING_WINDOW = 180


def start(kb_id: str, question: str) -> str:
    """A new conversation, titled by its first question."""
    title = " ".join(question.split())[:TITLE_LENGTH] or "New chat"
    conversation_id = str(uuid.uuid4())
    now = _now()
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO conversations (id, kb_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, kb_id, title, now, now),
        )
    return conversation_id


def check(kb_id: str, conversation_id: str) -> None:
    """The conversation must exist and belong to this knowledge base."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ? AND kb_id = ?", (conversation_id, kb_id)
        ).fetchone()
    if row is None:
        raise NotFound("That chat does not exist in this knowledge base.")


def touch(conversation_id: str) -> None:
    with db.connection() as conn:
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (_now(), conversation_id))


def list_for_kb(kb_id: str) -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM interactions i WHERE i.conversation_id = c.id) AS turns
            FROM conversations c
            WHERE c.kb_id = ?
            ORDER BY c.updated_at DESC, c.id
            LIMIT ?
            """,
            (kb_id, LIST_LIMIT),
        ).fetchall()
    return [dict(row) for row in rows]


def get(conversation_id: str) -> dict:
    """The conversation with every turn, oldest first, ready for the chat page."""
    with db.connection() as conn:
        chat = conn.execute(
            "SELECT id, kb_id, title, created_at, updated_at FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if chat is None:
            raise NotFound("That chat does not exist.")
        rows = conn.execute(
            audit._SELECT + " WHERE i.conversation_id = ? ORDER BY i.created_at, i.rowid", (conversation_id,)
        ).fetchall()
    return {**dict(chat), "turns": [_turn(audit._full(row)) for row in rows]}


def delete(conversation_id: str) -> None:
    """Forget the chat. Its answers stay in the audit log."""
    with db.connection() as conn:
        conn.execute("UPDATE interactions SET conversation_id = NULL WHERE conversation_id = ?", (conversation_id,))
        if conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount == 0:
            raise NotFound("That chat does not exist.")


def latest_for_kb(kb_id: str) -> str | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE kb_id = ? ORDER BY updated_at DESC, id LIMIT 1", (kb_id,)
        ).fetchone()
    return row["id"] if row else None


def _turn(item: dict) -> dict:
    llm = {"provider": item["llm_provider"], "model": item["llm_model"]} if item["mode"] == "llm" else None
    rerank = (
        {"provider": item["rerank_provider"], "model": item["rerank_model"]}
        if item["rerank_provider"] not in ("", "none")
        else None
    )
    pending = (
        item["mode"] == "llm"
        and not item["judged_at"]
        and not item["judge_error"]
        and time.time() - _epoch(item["created_at"]) < SCORING_WINDOW
    )
    suggesting = (
        item["mode"] == "llm"
        and item["followups"] is None
        and followups.enabled()
        and time.time() - _epoch(item["created_at"]) < SCORING_WINDOW
    )
    return {
        "id": item["id"],
        "question": item["question"],
        "created_at": item["created_at"],
        "result": {
            "knowledge_base": {"id": item["kb_id"], "name": item["kb_name"]},
            "answer": item["answer"],
            "mode": item["mode"],
            "llm": llm,
            "rerank": rerank,
            "elapsed_ms": item["elapsed_ms"],
            "citations": item["citations"],
            "interaction_id": item["id"],
            "scoring": pending,
            "suggesting": suggesting,
        },
        "interaction": {
            key: item[key]
            for key in (
                "rating",
                "rating_reason",
                "rating_comment",
                "faithfulness",
                "faithfulness_claims",
                "relevance",
                "relevance_reason",
                "judge_model",
                "judged_at",
                "judge_error",
                "followups",
            )
        },
    }


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp).timestamp()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
