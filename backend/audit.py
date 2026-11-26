"""Audit trail of answers and the feedback on them.

Every answer is stored with the question, the passages it cited, the models and
chunk settings in force, and later the judge's scores. Feedback is append-only:
a changed vote adds an event, so the history stays for review. Rows outlive the
knowledge base they came from, because the point is learning from them.
"""

import json
import uuid
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from typing import Any

from backend import db
from backend.errors import BadRequest, NotFound

RATINGS = ("up", "down", "none")
REASONS = ("incorrect", "incomplete", "wrong_sources", "unclear", "other")
MAX_COMMENT = 1000

_LATEST = """
    (SELECT rating FROM feedback f WHERE f.interaction_id = i.id ORDER BY f.id DESC LIMIT 1)
"""
_LATEST_REASON = "(SELECT reason FROM feedback f WHERE f.interaction_id = i.id ORDER BY f.id DESC LIMIT 1)"
_LATEST_COMMENT = "(SELECT comment FROM feedback f WHERE f.interaction_id = i.id ORDER BY f.id DESC LIMIT 1)"
_SELECT = f"""
    SELECT i.*, i.rowid AS seq, {_LATEST} AS latest_rating, {_LATEST_REASON} AS latest_reason, {_LATEST_COMMENT} AS latest_comment
    FROM interactions i
"""


def record(entry: dict[str, Any]) -> str:
    """Store one answer and return its id."""
    interaction_id = str(uuid.uuid4())
    row = {
        "id": interaction_id,
        "created_at": _now(),
        "citations": json.dumps(entry.pop("citations", [])),
        **entry,
    }
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    with db.connection() as conn:
        conn.execute(f"INSERT INTO interactions ({columns}) VALUES ({placeholders})", tuple(row.values()))
    return interaction_id


def get(interaction_id: str) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute(_SELECT + " WHERE i.id = ?", (interaction_id,)).fetchone()
        if row is None:
            raise NotFound("That answer is not in the audit log.")
        events = conn.execute(
            "SELECT rating, reason, comment, created_at FROM feedback WHERE interaction_id = ? ORDER BY id",
            (interaction_id,),
        ).fetchall()
    item = _full(row)
    item["feedback_events"] = [dict(event) for event in events]
    return item


def rate(interaction_id: str, rating: str, reason: str = "", comment: str = "") -> dict[str, Any]:
    """Append a feedback event. "none" withdraws an earlier vote."""
    if rating not in RATINGS:
        raise BadRequest("rating must be up, down, or none.")
    reason = (reason or "").strip()
    if reason and reason not in REASONS:
        raise BadRequest(f"reason must be one of: {', '.join(REASONS)}.")
    comment = (comment or "").strip()
    if len(comment) > MAX_COMMENT:
        raise BadRequest(f"Comments must be {MAX_COMMENT} characters or fewer.")
    if rating != "down":
        reason = ""
    with db.connection() as conn:
        if conn.execute("SELECT 1 FROM interactions WHERE id = ?", (interaction_id,)).fetchone() is None:
            raise NotFound("That answer is not in the audit log.")
        conn.execute(
            "INSERT INTO feedback (interaction_id, rating, reason, comment, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction_id, rating, reason, comment, _now()),
        )
    return get(interaction_id)


def save_scores(interaction_id: str, scores: dict[str, Any]) -> None:
    allowed = {
        "judge_model",
        "judged_at",
        "faithfulness",
        "faithfulness_claims",
        "relevance",
        "relevance_reason",
        "judge_error",
    }
    fields = {key: value for key, value in scores.items() if key in allowed}
    if "faithfulness_claims" in fields:
        fields["faithfulness_claims"] = json.dumps(fields["faithfulness_claims"])
    assignments = ", ".join(f"{key} = ?" for key in fields)
    with db.connection() as conn:
        conn.execute(f"UPDATE interactions SET {assignments} WHERE id = ?", (*fields.values(), interaction_id))


def save_followups(interaction_id: str, questions: list[dict[str, str]]) -> None:
    with db.connection() as conn:
        conn.execute("UPDATE interactions SET followups = ? WHERE id = ?", (json.dumps(questions), interaction_id))


def list_items(
    kb_id: str = "", rating: str = "", scored: str = "", limit: int = 50, offset: int = 0
) -> dict[str, Any]:
    """Newest first. rating: up, down, unrated. scored: yes, no, low (faithfulness under 70%)."""
    if rating not in ("", "up", "down", "unrated"):
        raise BadRequest("rating must be up, down, or unrated.")
    if scored not in ("", "yes", "no", "low"):
        raise BadRequest("scored must be yes, no, or low.")
    limit = max(1, min(int(limit), 200))
    where, params = _filters(kb_id, rating, scored)
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM ({_SELECT}) i {where} ORDER BY created_at DESC, seq DESC LIMIT ? OFFSET ?",
            (*params, limit, max(0, int(offset))),
        ).fetchall()
        total = conn.execute(f"SELECT COUNT(*) FROM ({_SELECT}) i {where}", params).fetchone()[0]
    return {"items": [_brief(row) for row in rows], "total": total, "summary": summary(kb_id)}


def summary(kb_id: str = "") -> dict[str, Any]:
    where, params = _filters(kb_id, "", "")
    with db.connection() as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*) AS total,
                   SUM(mode = 'llm') AS llm_answers,
                   SUM(latest_rating = 'up') AS up,
                   SUM(latest_rating = 'down') AS down,
                   SUM(judged_at IS NOT NULL) AS scored,
                   AVG(faithfulness) AS faithfulness,
                   AVG(relevance) AS relevance
            FROM ({_SELECT}) i {where}
            """,
            params,
        ).fetchone()
    up, down = row["up"] or 0, row["down"] or 0
    return {
        "total": row["total"],
        "llm_answers": row["llm_answers"] or 0,
        "up": up,
        "down": down,
        "rated": up + down,
        "approval": round(up / (up + down), 3) if up + down else None,
        "scored": row["scored"] or 0,
        "faithfulness": _round(row["faithfulness"]),
        "relevance": _round(row["relevance"]),
    }


def export(kb_id: str = "") -> Iterator[dict[str, Any]]:
    """Every matching answer in full, oldest first, with its feedback events."""
    where, params = _filters(kb_id, "", "")
    with db.connection() as conn:
        rows = conn.execute(f"SELECT * FROM ({_SELECT}) i {where} ORDER BY created_at, seq", params).fetchall()
        events: dict[str, list[dict]] = {}
        for event in conn.execute(
            "SELECT interaction_id, rating, reason, comment, created_at FROM feedback ORDER BY id"
        ):
            events.setdefault(event["interaction_id"], []).append(
                {key: event[key] for key in ("rating", "reason", "comment", "created_at")}
            )
    for row in rows:
        item = _full(row)
        item["feedback_events"] = events.get(item["id"], [])
        yield item


def purge(days: int) -> int:
    """Delete answers older than `days` days. Zero keeps everything."""
    if days <= 0:
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    with db.connection() as conn:
        removed = conn.execute("DELETE FROM interactions WHERE created_at < ?", (cutoff,)).rowcount
        if removed:
            conn.execute(
                "DELETE FROM conversations WHERE id NOT IN "
                "(SELECT conversation_id FROM interactions WHERE conversation_id IS NOT NULL)"
            )
        return removed


def _filters(kb_id: str, rating: str, scored: str) -> tuple[str, tuple]:
    clauses, params = [], []
    if kb_id:
        clauses.append("kb_id = ?")
        params.append(kb_id)
    if rating == "unrated":
        clauses.append("(latest_rating IS NULL OR latest_rating = 'none')")
    elif rating:
        clauses.append("latest_rating = ?")
        params.append(rating)
    if scored == "yes":
        clauses.append("judged_at IS NOT NULL")
    elif scored == "no":
        clauses.append("judged_at IS NULL AND mode = 'llm'")
    elif scored == "low":
        clauses.append("faithfulness IS NOT NULL AND faithfulness < 0.7")
    return ("WHERE " + " AND ".join(clauses) if clauses else ""), tuple(params)


def _suggestions(raw: str) -> list[dict[str, str]]:
    return [{"question": s, "kind": "deeper"} if isinstance(s, str) else s for s in json.loads(raw)]


def _full(row) -> dict[str, Any]:
    item = dict(row)
    item.pop("seq", None)
    item["citations"] = json.loads(item["citations"])
    item["followups"] = None if item.get("followups") is None else _suggestions(item["followups"])
    item["faithfulness_claims"] = json.loads(item["faithfulness_claims"])
    item["rating"] = None if item["latest_rating"] in (None, "none") else item["latest_rating"]
    item["rating_reason"] = item.pop("latest_reason") or ""
    item["rating_comment"] = item.pop("latest_comment") or ""
    del item["latest_rating"]
    return item


def _brief(row) -> dict[str, Any]:
    item = _full(row)
    item["citation_count"] = len(item.pop("citations"))
    return item


def _round(value):
    return None if value is None else round(value, 3)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
