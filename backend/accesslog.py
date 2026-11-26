"""Keep the uvicorn access log readable.

The platform and browsers request `GET /` over and over (health checks, page
reloads). The first successful one is logged, the repeats are dropped.
"""

import logging


class FirstRootRequestOnly(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.seen = False

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn logs: (client address, method, path, http version, status code)
        args = record.args
        if not (isinstance(args, tuple) and len(args) == 5):
            return True
        _client, method, path, _version, status = args
        if method == "GET" and path == "/" and status == 200:
            if self.seen:
                return False
            self.seen = True
        return True


def install() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, FirstRootRequestOnly) for item in logger.filters):
        logger.addFilter(FirstRootRequestOnly())
