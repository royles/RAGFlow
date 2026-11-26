"""SQLite catalog of knowledge bases and the files uploaded into them.

Vectors live in Qdrant. This database only remembers the settings and the
file names, so the UI can list them without scanning the index.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from backend.settings import data_dir

_KB_COLUMNS = (
    "name",
    "description",
    "chunk_size",
    "chunk_overlap",
    "nn_algorithm",
)

# Chat and region moved to the global model settings.
_LEGACY_COLUMNS = ("chat_provider", "chat_model", "bedrock_region")


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    database = data_dir() / "ragflow.db"
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init(conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            chunk_size INTEGER NOT NULL,
            chunk_overlap INTEGER NOT NULL,
            nn_algorithm TEXT NOT NULL,
            distance TEXT NOT NULL,
            embed_provider TEXT NOT NULL,
            embed_model TEXT NOT NULL,
            embed_dim INTEGER NOT NULL,
            collection TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY,
            kb_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            chunk_count INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE
        );
        """
    )
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(knowledge_bases)")}
    for column in _LEGACY_COLUMNS:
        if column in existing:
            conn.execute(f"ALTER TABLE knowledge_bases DROP COLUMN {column}")


def insert_kb(row: dict[str, Any]) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    with connection() as conn:
        conn.execute(
            f"INSERT INTO knowledge_bases ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )


def list_kbs() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT kb.*,
                   (SELECT COUNT(*) FROM documents d WHERE d.kb_id = kb.id) AS document_count
            FROM knowledge_bases kb
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [_with_count(row) for row in rows]


def get_kb(kb_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT kb.*,
                   (SELECT COUNT(*) FROM documents d WHERE d.kb_id = kb.id) AS document_count
            FROM knowledge_bases kb
            WHERE kb.id = ?
            """,
            (kb_id,),
        ).fetchone()
    return _with_count(row) if row else None


def find_by_name(name: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT kb.*,
                   (SELECT COUNT(*) FROM documents d WHERE d.kb_id = kb.id) AS document_count
            FROM knowledge_bases kb
            WHERE kb.name = ?
            ORDER BY created_at
            """,
            (name,),
        ).fetchall()
    return [_with_count(row) for row in rows]


def update_kb(kb_id: str, fields: dict[str, Any]) -> None:
    allowed = {key: value for key, value in fields.items() if key in _KB_COLUMNS}
    if not allowed:
        return
    assignments = ", ".join(f"{key} = ?" for key in allowed)
    with connection() as conn:
        conn.execute(
            f"UPDATE knowledge_bases SET {assignments} WHERE id = ?",
            (*allowed.values(), kb_id),
        )


def delete_kb(kb_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM knowledge_bases WHERE id = ?", (kb_id,))


def insert_document(row: dict[str, Any]) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    with connection() as conn:
        conn.execute(
            f"INSERT INTO documents ({columns}) VALUES ({placeholders})",
            tuple(row.values()),
        )


def list_documents(kb_id: str) -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, kb_id, filename, byte_size, chunk_count, created_at
            FROM documents
            WHERE kb_id = ?
            ORDER BY created_at DESC
            """,
            (kb_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_document(kb_id: str, doc_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            "DELETE FROM documents WHERE kb_id = ? AND id = ?",
            (kb_id, doc_id),
        )
    return cursor.rowcount > 0


def _with_count(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["document_count"] = data.pop("document_count")
    return data
