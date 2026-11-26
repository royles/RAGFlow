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


def sample_path() -> Path:
    return ROOT / "samples" / "handbook.txt"
