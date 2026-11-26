#!/usr/bin/env python3
"""Cloudera AI application entry. UI, REST, and MCP share one loopback port."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn


def port() -> int:
    for name in ("CDSW_APP_PORT", "APP_PORT"):
        value = os.environ.get(name, "").strip()
        if value:
            return int(value)
    return 8000


def main() -> None:
    chosen = port()
    print(f"RAGFlow listening on http://127.0.0.1:{chosen}", flush=True)
    print(f"MCP endpoint http://127.0.0.1:{chosen}/mcp", flush=True)
    uvicorn.run("backend.main:app", host="127.0.0.1", port=chosen, workers=1)


if __name__ == "__main__":
    main()
