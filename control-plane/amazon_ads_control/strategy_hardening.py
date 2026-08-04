from __future__ import annotations

from typing import Any

from .strategy_gold import OptimizationEngine

_INSTALLED = False

ROW_REQUIREMENTS: dict[str, tuple[tuple[str, ...], ...]] = {
    "targets": (
        ("target_id", "keyword_id", "id"), ("campaign_id",), ("ad_group_id",), ("ad_product",),
        ("clicks",), ("spend",), ("sales",), ("orders",), ("bid",),
    ),
    "search_terms": (
        ("search_term", "query"), ("campaign_id",), ("ad_group_id",), ("ad_product",),
        ("clicks",), ("spend",), ("sales",), ("orders",),
    ),
    "campaigns": (
        ("campaign_id", "id"), ("ad_product",), ("clicks",), ("spend",),
        ("sales",), ("orders",), ("budget",),
    ),
    "placements": (
        ("campaign_id",), ("ad_product",), ("placement",), ("clicks",), ("spend",),
        ("sales",), ("orders",), ("adjustment_percent",),
    ),
}


def _has_any(row: dict[str, Any], aliases: tuple[str, ...]) -> bool:
    return any(alias in row and row[alias] not in (None, "") for alias in aliases)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original_quality = OptimizationEngine._quality

    def quality(self, snapshot, policy):
        result = original_quality(self, snapshot, policy)
        unsafe = set(result.get("missing_or_unsafe", []))
        warnings = set(result.get("warnings", []))
        profile = snapshot.get("profile") if isinstance(snapshot.get("profile"), dict) else {}
        for field in ("marketplace", "currency"):
            if not str(profile.get(field) or "").strip():
                unsafe.add(f"missing_profile_{field}")
        account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
        for metric in ("impressions", "clicks", "spend", "sales", "orders"):
            if metric not in account or account.get(metric) in (None, ""):
                unsafe.add(f"missing_account_{metric}")
        allowed_products = {
            str(item).upper() for item in getattr(policy, "auto_write_ad_products", (
                "SPONSORED_PRODUCTS", "SPONSORED_BRANDS", "SPONSORED_DISPLAY",
            ))
        }
        rejected = result.setdefault("rejected_rows", {})
        for level, requirements in ROW_REQUIREMENTS.items():
            rows = snapshot.get(level, [])
            if not isinstance(rows, list):
                continue
            bad = rejected.setdefault(level, {})
            for index, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                reasons = list(bad.get(str(index), []))
                for aliases in requirements:
                    if not _has_any(row, aliases):
                        reasons.append("missing_" + "_or_".join(aliases))
                product = str(row.get("ad_product") or "").upper()
                if product and product not in allowed_products:
                    reasons.append("ad_product_observe_only")
                if reasons:
                    bad[str(index)] = sorted(set(reasons))
            if bad:
                warnings.add(f"{level}_rows_rejected")
        result["missing_or_unsafe"] = sorted(unsafe)
        result["warnings"] = sorted(warnings)
        result["eligible_for_writes"] = not unsafe
        result["auto_write_ad_products"] = sorted(allowed_products)
        return result

    def identity(row: dict[str, Any]) -> str:
        text = row.get("keyword_text") or row.get("targeting_text") or row.get("expression") or row.get("asin") or ""
        match = str(row.get("match_type") or row.get("matchType") or "").upper()
        if not text or match not in {"EXACT", "TARGETING_EXPRESSION"}:
            return ""
        scope = row.get("overlap_scope") or row.get("ad_group_id") or row.get("campaign_id") or ""
        product = str(row.get("ad_product") or "").upper()
        normalized = " ".join(str(text).strip().lower().split())
        return f"{product}:{scope}:{match}:{normalized}"

    OptimizationEngine._quality = quality
    OptimizationEngine._identity = staticmethod(identity)
    _INSTALLED = True
