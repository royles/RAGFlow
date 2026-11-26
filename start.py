#!/usr/bin/env python3
"""Cloudera AI application entry. UI, REST, and MCP share one loopback port.

Works as a script (`python3 start.py`) and when its code is run in a notebook
cell, where `__file__` does not exist and an event loop is already running.
"""

import asyncio
import os
import sys
import threading
from pathlib import Path


def project_root() -> Path:
    """The RAGFlow folder: beside this file, else found from the working directory."""
    try:
        return Path(__file__).resolve().parent
    except NameError:
        pass
    configured = os.environ.get("RAGFLOW_ROOT", "").strip()
    here = Path.cwd().resolve()
    for folder in ([Path(configured)] if configured else []) + [here, *here.parents]:
        if (folder / "backend" / "main.py").is_file():
            return folder
    raise SystemExit(
        "Could not find the RAGFlow project. Change to the project folder first, or set RAGFLOW_ROOT to its path."
    )


ROOT = project_root()
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn


def port() -> int:
    value = os.environ.get("CDSW_APP_PORT", "").strip()
    if not value:
        raise SystemExit("CDSW_APP_PORT is not set. Cloudera AI sets it for applications; set it yourself to run locally.")
    return int(value)


def main() -> "uvicorn.Server | None":
    chosen = port()
    print(f"RAGFlow listening on http://127.0.0.1:{chosen}", flush=True)
    print(f"MCP endpoint http://127.0.0.1:{chosen}/mcp", flush=True)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        uvicorn.run("backend.main:app", host="127.0.0.1", port=chosen, workers=1)
        return None
    # A notebook already runs an event loop, and uvicorn.run would fail inside it.
    # Serve from a background thread so the cell returns and the app keeps running.
    server = uvicorn.Server(uvicorn.Config("backend.main:app", host="127.0.0.1", port=chosen, workers=1))
    threading.Thread(target=server.run, name="ragflow", daemon=True).start()
    print("Running in the background. To stop it: server.should_exit = True", flush=True)
    return server


if __name__ == "__main__":
    server = main()
