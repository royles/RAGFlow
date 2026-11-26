"""Run indexing work on several threads at once.

Embedding is waiting on a remote model, so batches overlap well. Two limits keep
that polite: each file runs at most `embed_workers` batches at a time, and a gate
caps the embedding calls in flight across every file being indexed together.
"""

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager

from backend import advanced

_gate = threading.Condition()
_in_flight = 0


def workers() -> int:
    return max(1, advanced.get("embed_workers"))


@contextmanager
def slot():
    """Hold one of the embedding slots. The limit is read each time, so changing it applies at once."""
    global _in_flight
    with _gate:
        while _in_flight >= workers():
            _gate.wait(timeout=1.0)
        _in_flight += 1
    try:
        yield
    finally:
        with _gate:
            _in_flight -= 1
            _gate.notify()


def run(items: Iterable, work: Callable[[object], None], limit: int | None = None) -> None:
    """Call work(item) for every item on up to `limit` threads.

    The first failure stops new work, lets running calls finish, and is raised.
    """
    limit = limit or workers()
    stop = threading.Event()
    failures: list[BaseException] = []

    def task(item):
        if stop.is_set():
            return
        try:
            work(item)
        except BaseException as exc:
            failures.append(exc)
            stop.set()

    pending: set = set()
    with ThreadPoolExecutor(max_workers=limit, thread_name_prefix="index") as pool:
        for item in items:
            if stop.is_set():
                break
            if len(pending) >= limit * 2:
                _, pending = wait(pending, return_when=FIRST_COMPLETED)
            pending.add(pool.submit(task, item))
    if failures:
        raise failures[0]
