"""Start an ASGI app on a free loopback port for the length of a test class."""

import socket
import threading
import time

import uvicorn


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, app):
        self.port = free_port()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> "Server":
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("Test server did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


_shared: Server | None = None


def shared_app_server() -> Server:
    """The RAGFlow app, started once per process.

    The MCP session manager can only run once per app object, so every test
    module has to share one server.
    """
    global _shared
    if _shared is None:
        from backend.main import app

        _shared = Server(app).start()
    return _shared
