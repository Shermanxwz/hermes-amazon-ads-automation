from __future__ import annotations

import threading
from typing import Any

from .service import ControlService

_LOCK = threading.RLock()
_INSTALLED = False


def install() -> None:
    """Serialize result-event reservation, execution recording and finalization.

    The control plane is a single systemd instance. Serializing this short
    SQLite path prevents a duplicate HTTP callback from being acknowledged
    while the first callback is still between event registration and execution
    state commit.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    current = ControlService.finish_tool

    def finish_tool(self: ControlService, payload: dict[str, Any]) -> dict[str, Any]:
        with _LOCK:
            return current(self, payload)

    ControlService.finish_tool = finish_tool
    _INSTALLED = True
