from __future__ import annotations

from typing import Any

_INSTALLED = False
_ALLOWED_SOURCES = {
    "hermes-registry:na",
    "hermes-registry:eu",
    "hermes-registry:fe",
}


def _catalog_source(raw: dict[str, Any]) -> str:
    source = str(raw.get("source") or "").strip().lower()
    if not source:
        # Keep legacy/unknown discovery visible in the catalog, but untagged.
        # The regional authorization layer will fail closed on every such tool.
        return "hermes-registry"
    if source not in _ALLOWED_SOURCES:
        raise ValueError(
            "catalog tool source must be one of hermes-registry:na, "
            "hermes-registry:eu or hermes-registry:fe"
        )
    return source


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    from .catalog import descriptor_from_payload, is_registered_amazon_tool
    from .service import ControlService

    def sync_catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_tools = payload.get("tools")
        if not isinstance(raw_tools, list) or not raw_tools:
            raise ValueError("tools must be a non-empty array")
        tools = []
        for raw in raw_tools:
            if not isinstance(raw, dict):
                raise ValueError("each catalog tool must be an object")
            # Semantics, family, risk and Schema hash remain controller-derived.
            # Only the exact regional registry identity is accepted from Hermes.
            sanitized = {
                "registered_name": raw.get("registered_name") or raw.get("name"),
                "native_name": raw.get("native_name"),
                "server_name": raw.get("server_name") or "amazon-ads",
                "schema": raw.get("schema") if isinstance(raw.get("schema"), dict) else {},
                "enabled": bool(raw.get("enabled", True)),
                "source": _catalog_source(raw),
            }
            descriptor = descriptor_from_payload(sanitized)
            if not is_registered_amazon_tool(descriptor.registered_name):
                raise ValueError(
                    f"tool is outside mcp-amazon-ads: {descriptor.registered_name}"
                )
            tools.append(descriptor)
        return self.store.sync_catalog(tools)

    ControlService.sync_catalog = sync_catalog
    _INSTALLED = True
