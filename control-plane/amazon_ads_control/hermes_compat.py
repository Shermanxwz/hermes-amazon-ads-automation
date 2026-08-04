from __future__ import annotations

_INSTALLED = False


def install() -> None:
    """Apply defaults that are guaranteed by Hermes 0.18.x.

    Hermes reliably supplies isolated child sessions and role markers. Per-child
    model overrides are deployment/version dependent, so model diversity is an
    optional hardening policy rather than a default availability requirement.
    """
    global _INSTALLED
    if _INSTALLED:
        return
    from . import db as db_module
    from .service import ControlService

    db_module.DEFAULT_SETTINGS.update({
        "require_declared_worker_model": False,
        "require_different_verifier_model": False,
        "executor_models": [],
        "verifier_models": [],
        "prefer_different_verifier_model": True,
        "hermes_minimum_version": "0.18.2",
    })
    db_module.BOOLEAN_SETTINGS.add("prefer_different_verifier_model")

    original_context = ControlService.context

    def context(self, session_id):
        result = original_context(self, session_id)
        result["hermes_compatibility"] = {
            "minimum_version": self.store.get_settings().get("hermes_minimum_version", "0.18.2"),
            "different_session_verifier": "required",
            "different_model_verifier": (
                "required" if self.store.get_settings().get("require_different_verifier_model")
                else "preferred_when_supported"
            ),
            "command_approval": "feature-detected",
            "fallback_policy": "observe when model identity is unavailable or untrusted",
        }
        return result

    ControlService.context = context
    _INSTALLED = True
