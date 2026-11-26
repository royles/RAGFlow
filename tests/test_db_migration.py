"""The schema upgrade must be safe when several requests arrive at once."""

import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("RAGFLOW_DATA_DIR", tempfile.mkdtemp(prefix="ragflow-import-"))

from backend import db

OLD_SCHEMA = """
CREATE TABLE knowledge_bases (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
  chunk_size INTEGER NOT NULL, chunk_overlap INTEGER NOT NULL, distance TEXT NOT NULL, nn_algorithm TEXT NOT NULL,
  embed_provider TEXT NOT NULL, embed_model TEXT NOT NULL, embed_dim INTEGER NOT NULL, collection TEXT NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE documents (id TEXT PRIMARY KEY, kb_id TEXT NOT NULL, filename TEXT NOT NULL, byte_size INTEGER NOT NULL,
  chunk_count INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE interactions (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, source TEXT NOT NULL, kb_id TEXT NOT NULL,
  kb_name TEXT NOT NULL, question TEXT NOT NULL, answer TEXT NOT NULL, mode TEXT NOT NULL);
"""


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.previous = os.environ.get("RAGFLOW_DATA_DIR")
        os.environ["RAGFLOW_DATA_DIR"] = self.tmp.name
        with sqlite3.connect(Path(self.tmp.name) / "ragflow.db") as conn:
            conn.executescript(OLD_SCHEMA)

    def tearDown(self):
        os.environ["RAGFLOW_DATA_DIR"] = self.previous
        self.tmp.cleanup()

    def columns(self, table):
        with sqlite3.connect(Path(self.tmp.name) / "ragflow.db") as conn:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}

    def test_running_the_upgrade_again_is_harmless(self):
        conn = sqlite3.connect(Path(self.tmp.name) / "ragflow.db")
        conn.row_factory = sqlite3.Row
        db._init(conn)
        db._init(conn)
        conn.close()
        self.assertIn("source_url", self.columns("documents"))
        self.assertTrue({"conversation_id", "followups"} <= self.columns("interactions"))

    def test_a_column_added_by_someone_else_first_is_not_an_error(self):
        with sqlite3.connect(Path(self.tmp.name) / "ragflow.db") as conn:
            conn.execute("ALTER TABLE documents ADD COLUMN source_url TEXT NOT NULL DEFAULT ''")
        conn = sqlite3.connect(Path(self.tmp.name) / "ragflow.db")
        conn.row_factory = sqlite3.Row
        db._init(conn)
        conn.close()

    def test_many_requests_at_once_after_an_upgrade_all_succeed(self):
        db._ready.discard(str(Path(self.tmp.name) / "ragflow.db"))
        errors, barrier = [], threading.Barrier(16)

        def request():
            try:
                barrier.wait()
                db.list_kbs()
                db.list_documents("none")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=request) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertIn("source_url", self.columns("documents"))

    def test_the_upgrade_runs_once_per_database_not_per_connection(self):
        key = str(Path(self.tmp.name) / "ragflow.db")
        db._ready.discard(key)
        calls = []
        real = db._init
        db._init = lambda conn: (calls.append(1), real(conn))[1]
        try:
            for _ in range(5):
                db.list_kbs()
        finally:
            db._init = real
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
