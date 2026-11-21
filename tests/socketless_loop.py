"""In-memory ASGI test loop when the sandbox cannot create a socketpair.

Opt in with CLOUD_DB_TEST_SOCKETLESS=1. No network I/O is supported.
Thread wakeups are polled every 10ms. Production never imports this module.
"""

import asyncio
import selectors
import threading


class NoIOSelector(selectors.SelectSelector):
    def select(self, timeout=None):
        if self.get_map():
            raise RuntimeError("Socketless test loop does not support network I/O")
        threading.Event().wait(min(timeout if timeout is not None else 0.01, 0.01))
        return []


class SocketlessLoop(asyncio.SelectorEventLoop):
    def __init__(self):
        super().__init__(NoIOSelector())

    def _make_self_pipe(self):
        self._ssock = self._csock = None

    def _close_self_pipe(self):
        pass

    def _write_to_self(self):
        pass
