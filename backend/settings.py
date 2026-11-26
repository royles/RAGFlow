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


def max_chunks() -> int:
    """Most chunks one upload may produce. 0 means no limit."""
    from backend import advanced

    return advanced.get("max_chunks")


def sample_path() -> Path:
    return ROOT / "samples" / "handbook.txt"


def audit_retention_days() -> int:
    """Days to keep answers in the audit log. 0 keeps them all."""
    from backend import advanced

    return advanced.get("audit_retention_days")


def max_upload_bytes() -> int:
    """Largest file one upload may carry, in bytes. 0 means no limit."""
    from backend import advanced

    return advanced.get("max_upload_mb") * 1024 * 1024
