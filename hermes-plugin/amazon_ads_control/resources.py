from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _integer_env(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _memory_total_mb() -> int:
    override = _integer_env("ADS_CONTROL_MEMORY_MB")
    if override:
        return override
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return max(1, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return max(1, int(pages * page_size // 1024 // 1024))
    except (AttributeError, OSError, ValueError):
        return 2048


def snapshot() -> dict[str, Any]:
    """Return a capability-preserving runtime concurrency profile.

    Hardware changes alter only parallelism and streaming chunk sizes. Strategy,
    verification, risk controls and supported Amazon Ads operations are never
    disabled because a host is small.
    """
    cpu = _integer_env("ADS_CONTROL_CPU_COUNT") or max(1, os.cpu_count() or 1)
    memory_mb = _memory_total_mb()
    try:
        load_1m = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        load_1m = 0.0
    pressure = load_1m / cpu if cpu else 0.0

    if cpu <= 2 or memory_mb <= 2304:
        tier = "constrained"
        max_profiles = 1
        max_children = 1
        chunk_rows = 2500
        max_in_memory_reports = 1
    elif cpu <= 2 or memory_mb <= 4608:
        tier = "balanced"
        max_profiles = 2
        max_children = 2
        chunk_rows = 5000
        max_in_memory_reports = 1
    else:
        tier = "expanded"
        max_profiles = min(4, cpu)
        max_children = min(4, max(2, cpu - 1))
        chunk_rows = 10000
        max_in_memory_reports = 2

    # High transient load only serializes non-urgent collection. It does not
    # weaken strategy, verification or fail-closed controls.
    if pressure >= 1.25:
        max_profiles = 1
        max_children = 1

    return {
        "tier": tier,
        "cpu_count": cpu,
        "memory_total_mb": memory_mb,
        "load_1m": round(load_1m, 2),
        "load_per_cpu": round(pressure, 2),
        "max_concurrent_profiles": max_profiles,
        "max_concurrent_children": max_children,
        "report_stream_chunk_rows": chunk_rows,
        "max_in_memory_reports": max_in_memory_reports,
        "defer_nonurgent_collection": pressure >= 1.25,
        "feature_reduction": False,
        "browser_automation_enabled": False,
    }
