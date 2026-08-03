#!/usr/local/lib/hermes-agent/venv/bin/python
"""Sync the remote New-API model catalog into Hermes providers."""
from __future__ import annotations
import json, sys, time, urllib.request
from pathlib import Path
import yaml

CFG = Path("/root/.hermes/config.yaml")
API_BASE = "https://api.230385.xyz/v1"
CHAT_PROVIDER = "new-api-230385"
CODEX_PROVIDER = "new-api-230385-codex"
LOCK = Path("/root/.hermes/state/newapi-sync.lock")
CHAT_TYPES = ("chat_completions",)
CODEX_PREFIXES = ("gpt-",)
CHAT_MAX_INPUT = 200000
CHAT_MAX_OUTPUT = 32768
CODEX_MAX_INPUT = 1000000
CODEX_MAX_OUTPUT = 65536


def fetch_model_ids() -> set[str]:
    cfg = yaml.safe_load(CFG.read_text()) or {}
    key = (((cfg.get("providers") or {}).get(CHAT_PROVIDER) or {}).get("api_key"))
    if not key:
        raise RuntimeError("New-API key is missing from Hermes config")
    req = urllib.request.Request(
        f"{API_BASE}/models",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        body = json.load(response)
    ids = {item.get("id") for item in body.get("data", []) if isinstance(item, dict)}
    return {mid for mid in ids if isinstance(mid, str) and mid.strip()}


def split_models(ids: set[str]) -> tuple[set[str], set[str]]:
    # The New-API deployment exposes the gpt-* models through its Codex
    # channel.  They must use Responses/codex_responses; the proxy explicitly
    # rejects /v1/chat/completions for that channel.  Keep the other models on
    # the ordinary OpenAI-compatible chat-completions route.
    codex = {
        model_id for model_id in ids
        if model_id.lower().startswith(CODEX_PREFIXES)
    }
    return ids - codex, codex


def protected_refs(cfg: dict) -> set[str]:
    refs: set[str] = set()
    model = cfg.get("model") or {}
    if isinstance(model, dict):
        for key in ("default", "provider"):
            val = model.get(key)
            if isinstance(val, str):
                refs.add(val)
    for item in cfg.get("fallback_providers", []) or []:
        if isinstance(item, dict):
            for key in ("provider", "model"):
                val = item.get(key)
                if isinstance(val, str):
                    refs.add(val)
    return refs


def entry(model_id: str, codex: bool) -> dict:
    return {
        "model": model_id,
        "max_input_tokens": CODEX_MAX_INPUT if codex else CHAT_MAX_INPUT,
        "max_output_tokens": CODEX_MAX_OUTPUT if codex else CHAT_MAX_OUTPUT,
        "supports_reasoning": True,
    }


def sync_provider(cfg: dict, slug: str, desired: set[str], codex: bool) -> tuple[list[str], list[str], list[str]]:
    providers = cfg.setdefault("providers", {})
    provider = providers.setdefault(slug, {})
    provider["base_url"] = API_BASE
    provider["api_mode"] = "codex_responses" if codex else "chat_completions"
    models = provider.setdefault("models", {})
    existing = set(models)
    refs = protected_refs(cfg)
    added, removed, kept = [], [], []
    for model_id in sorted(desired - existing):
        models[model_id] = entry(model_id, codex)
        added.append(model_id)
    for model_id in sorted(existing - desired):
        if model_id in refs or f"custom:{slug}" in refs:
            kept.append(model_id)
            continue
        models.pop(model_id, None)
        removed.append(model_id)
    return added, removed, kept


def main() -> int:
    if not CFG.exists():
        print(f"config not found: {CFG}")
        return 2
    ids = fetch_model_ids()
    if not ids:
        print("New-API returned no models")
        return 1
    chat, codex = split_models(ids)
    cfg = yaml.safe_load(CFG.read_text()) or {}
    a1, r1, k1 = sync_provider(cfg, CHAT_PROVIDER, chat, False)
    a2, r2, k2 = sync_provider(cfg, CODEX_PROVIDER, codex, True)
    added, removed, kept = a1 + a2, r1 + r2, k1 + k2
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if added or removed:
        backup = CFG.with_name("config.yaml.bak-newapi-sync-" + time.strftime("%Y%m%d-%H%M%S"))
        backup.write_text(CFG.read_text())
        CFG.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False))
        CFG.chmod(0o600)
        LOCK.write_text(",".join(added + [f"-{x}" for x in removed]))
    else:
        LOCK.touch()
    print(f"remote_models={len(ids)} chat={len(chat)} codex={len(codex)} added={len(added)} removed={len(removed)} kept={len(kept)}")
    if added:
        print("added:", ", ".join(added))
    if removed:
        print("removed:", ", ".join(removed))
    if kept:
        print("kept-stale:", ", ".join(kept))
    return 0


if __name__ == "__main__":
    sys.exit(main())
