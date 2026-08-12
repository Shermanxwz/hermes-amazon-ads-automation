from __future__ import annotations

from importlib import import_module
import threading
from typing import Final

EXTENSION_ORDER: Final[tuple[str, ...]] = (
    "closed_loop", "closed_loop_fixes", "strategy_hardening", "report_evidence_hardening",
    "callback_hardening", "task_hardening", "storage_maintenance", "storage_alert_rollup",
    "api_extension", "approval_gate", "approval_hardening", "regional_mcp",
    "catalog_region_hardening", "structural_execution", "structural_hardening",
    "write_batch_hardening", "approval_contract_fixes", "sealed_autonomy",
    "hermes_compat", "hermes_lifecycle", "http_disconnect_hardening",
    "verification_hardening", "sealed_activation", "sealed_activation_trust",
    "sealed_activation_outcomes", "budget_guard", "budget_reservation",
)

_LOCK = threading.Lock()
_INSTALLED: tuple[str, ...] = ()


def install_extensions() -> tuple[str, ...]:
    global _INSTALLED
    with _LOCK:
        if _INSTALLED:
            return _INSTALLED
        if len(set(EXTENSION_ORDER)) != len(EXTENSION_ORDER):
            raise RuntimeError("duplicate Amazon Ads runtime extension")
        installed: list[str] = []
        for name in EXTENSION_ORDER:
            module = import_module(f"{__package__}.{name}")
            installer = getattr(module, "install", None)
            if not callable(installer):
                raise RuntimeError(f"runtime extension {name} has no callable install()")
            installer()
            installed.append(name)
        from .api import Handler
        Handler.server_version = "HermesAdsControl/4.2"
        _INSTALLED = tuple(installed)
        return _INSTALLED


def installed_extensions() -> tuple[str, ...]:
    return _INSTALLED
