from __future__ import annotations

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    from . import sealed_activation
    from .sealed_plan import INTERNAL_VERIFIED_CREATE

    original_activation_action = sealed_activation._activation_action

    def activation_action(create_action, tool, arguments):
        action = original_activation_action(create_action, tool, arguments)
        # This object-identity marker cannot be supplied through JSON or a
        # Hermes tool call. validate_standing_plan consumes and removes it
        # before persistence, then mints the standing authorization marker.
        action["_internal_verified_create"] = INTERNAL_VERIFIED_CREATE
        return action

    sealed_activation._activation_action = activation_action
    _INSTALLED = True
