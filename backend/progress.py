"""Live progress of indexing jobs, so the page can show how far a file has got.

Held in memory and keyed by an id the browser chooses. Progress is only needed
while a file is being indexed, so old entries are dropped.
"""

import threading
import time

MAX_AGE_SECONDS = 3600
MAX_ENTRIES = 500

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


def start(job_id: str, filename: str) -> None:
    if not job_id:
        return
    with _lock:
        _drop_old()
        _jobs[job_id] = {"phase": "reading", "filename": filename, "chunks": 0, "embedded": 0, "stored": 0, "touched": time.time()}


def update(job_id: str, **fields) -> None:
    if not job_id:
        return
    with _lock:
        job = _jobs.get(job_id)
        if job is not None:
            job.update(fields, touched=time.time())


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return None if job is None else {key: value for key, value in job.items() if key != "touched"}


def _drop_old() -> None:
    cutoff = time.time() - MAX_AGE_SECONDS
    for key in [key for key, job in _jobs.items() if job["touched"] < cutoff]:
        del _jobs[key]
    while len(_jobs) >= MAX_ENTRIES:
        del _jobs[min(_jobs, key=lambda key: _jobs[key]["touched"])]
