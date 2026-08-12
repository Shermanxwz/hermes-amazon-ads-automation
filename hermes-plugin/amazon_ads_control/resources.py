from __future__ import annotations

import math
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


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _host_memory_mb() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return max(1, int(line.split()[1]) // 1024)
    except (OSError, ValueError, IndexError):
        pass
    try:
        return max(1, int(os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE") // 1024 // 1024))
    except (AttributeError, OSError, ValueError):
        return 2048


def _cgroup_memory_mb() -> int | None:
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read_text(path)
        if not raw or raw == "max":
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # Ignore effectively-unlimited sentinel values.
        if 0 < value < (1 << 60):
            return max(1, value // 1024 // 1024)
    return None


def _cgroup_cpu_count() -> int | None:
    raw = _read_text("/sys/fs/cgroup/cpu.max")
    if raw:
        parts = raw.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    return max(1, math.ceil(quota / period))
            except ValueError:
                pass
    quota = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    period = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    try:
        if quota and period and int(quota) > 0 and int(period) > 0:
            return max(1, math.ceil(int(quota) / int(period)))
    except ValueError:
        pass
    return None


def _memory_total_mb() -> tuple[int, str]:
    override = _integer_env("ADS_CONTROL_MEMORY_MB")
    if override:
        return override, "environment"
    host = _host_memory_mb()
    cgroup = _cgroup_memory_mb()
    if cgroup:
        return min(host, cgroup), "cgroup"
    return host, "host"


def _cpu_count() -> tuple[int, str]:
    override = _integer_env("ADS_CONTROL_CPU_COUNT")
    if override:
        return override, "environment"
    host = max(1, os.cpu_count() or 1)
    cgroup = _cgroup_cpu_count()
    if cgroup:
        return min(host, cgroup), "cgroup"
    return host, "host"


def snapshot() -> dict[str, Any]:
    """Return a capability-preserving profile using effective cgroup limits."""
    cpu, cpu_source = _cpu_count()
    memory_mb, memory_source = _memory_total_mb()
    try:
        load_1m = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        load_1m = 0.0
    pressure = load_1m / cpu if cpu else 0.0

    if memory_mb <= 2304:
        tier, max_profiles, max_children, chunk_rows, max_reports = "constrained", 1, 1, 2500, 1
    elif cpu <= 2 or memory_mb <= 4608:
        tier, max_profiles, max_children, chunk_rows, max_reports = "balanced", 2, 2, 5000, 1
    else:
        tier = "expanded"
        max_profiles = min(4, cpu)
        max_children = min(4, max(2, cpu - 1))
        chunk_rows, max_reports = 10000, 2

    if pressure >= 1.25:
        max_profiles = 1
        max_children = 1

    return {
        "tier": tier,
        "cpu_count": cpu,
        "cpu_limit_source": cpu_source,
        "memory_total_mb": memory_mb,
        "memory_limit_source": memory_source,
        "load_1m": round(load_1m, 2),
        "load_per_cpu": round(pressure, 2),
        "max_concurrent_profiles": max_profiles,
        "max_concurrent_children": max_children,
        "report_stream_chunk_rows": chunk_rows,
        "max_in_memory_reports": max_reports,
        "defer_nonurgent_collection": pressure >= 1.25,
        "feature_reduction": False,
        "browser_automation_enabled": False,
    }
