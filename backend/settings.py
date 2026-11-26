"""Paths and environment for a Cloudera AI project."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def data_dir() -> Path:
    """Directory for the SQLite catalog and the embedded Qdrant index."""
    raw = os.environ.get("RAGFLOW_DATA_DIR", "data").strip() or "data"
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


DEFAULT_MAX_CHUNKS = 0


def max_chunks() -> int:
    """Most chunks one upload may produce. Unset or 0 means no limit.

    Every chunk is embedded, and Bedrock embeddings are one request per chunk,
    so the limit keeps a single upload from running past the proxy timeout.
    """
    raw = os.environ.get("RAGFLOW_MAX_CHUNKS", "").strip()
    if not raw:
        return DEFAULT_MAX_CHUNKS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CHUNKS
    return value if value >= 0 else DEFAULT_MAX_CHUNKS


def sample_path() -> Path:
    return ROOT / "samples" / "handbook.txt"


def audit_retention_days() -> int:
    """Days to keep answers in the audit log. RAGFLOW_AUDIT_RETENTION_DAYS=0 (the default) keeps them all."""
    try:
        return max(0, int(os.environ.get("RAGFLOW_AUDIT_RETENTION_DAYS", "").strip() or 0))
    except ValueError:
        return 0


def max_upload_bytes() -> int:
    """Largest file one upload may carry. RAGFLOW_MAX_UPLOAD_MB unset or 0 means no limit.

    The file is read into memory, so set a limit if the runtime has little RAM.
    """
    try:
        megabytes = int(os.environ.get("RAGFLOW_MAX_UPLOAD_MB", "").strip() or 0)
    except ValueError:
        return 0
    return max(0, megabytes) * 1024 * 1024
