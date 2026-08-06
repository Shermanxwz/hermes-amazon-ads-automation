from __future__ import annotations

from typing import Any

_INSTALLED = False
_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)


def install() -> None:
    """Treat a browser-aborted response as transport completion.

    Dashboard reloads, route replacement and the two-phase fail-closed mode
    transition can cancel an obsolete GET after the server has already begun
    writing its response. That is not an application failure and must not
    create an audit error or a socketserver traceback.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    from .api import Handler

    original_respond = Handler._respond
    original_static = Handler._static

    def respond(
        self: Handler,
        status: int,
        data: Any,
        headers: dict[str, str] | None = None,
    ) -> None:
        try:
            original_respond(self, status, data, headers)
        except _DISCONNECT_ERRORS:
            self.close_connection = True

    def static(self: Handler, filename: str) -> None:
        try:
            original_static(self, filename)
        except _DISCONNECT_ERRORS:
            self.close_connection = True

    Handler._respond = respond
    Handler._static = static
    _INSTALLED = True
