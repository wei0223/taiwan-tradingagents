"""Read-only valuation audits; descriptive history never certifies a fair multiple."""

import math
import re
from statistics import median

from .scenarios import timestamp


def quarter_window(latest_reported):
    """Latest *verified earnings* quarter, not the latest monthly revenue quarter."""
    if not re.fullmatch(r"\d{4}-Q[1-4]", latest_reported):
        raise ValueError("Explicit latest verified earnings quarter required")
    year, quarter = int(latest_reported[:4]), int(latest_reported[-1])
    index = year * 4 + quarter - 1
    return [f"{n // 4}-Q{n % 4 + 1}" for n in range(index - 3, index + 1)]


def eps_inventory(stock_id, latest_reported, evidence, cutoff):
    """Evidence attestation is supplied by an original-document review, not an LLM guess."""
    periods = quarter_window(latest_reported)
    rows = []
    for period in periods:
        candidates = [r for r in evidence if r["period"] == period]
        if len(candidates) > 1:
            raise ValueError("Select a revision explicitly; do not choose EPS opportunistically")
        row = candidates[0] if candidates else {}
        reasons = []
        if not row:
            reasons.append("no_selected_original")
        else:
            for key, expected in {
                "stock_id": stock_id,
                "currency": "TWD",
                "scope": "consolidated",
                "basis": "direct_reported_single_quarter_diluted_eps",
            }.items():
                if row.get(key) != expected:
                    reasons.append(f"invalid_{key}")
            for key in ("source_sha256", "source_url", "locator", "reviewed_by", "captured_at"):
                if not row.get(key):
                    reasons.append(f"missing_{key}")
            if row.get("source_sha256") and not re.fullmatch(r"[a-f0-9]{64}", row["source_sha256"]):
                reasons.append("invalid_source_sha256")
            if not row.get("original_bytes_verified"):
                reasons.append("original_bytes_unverified")
            if row.get("captured_at") and timestamp(row["captured_at"]) > timestamp(cutoff):
                reasons.append("captured_after_cutoff")
            if row.get("published_at") and timestamp(row["published_at"]) > timestamp(cutoff):
                reasons.append("published_after_cutoff")
            eps = row.get("eps_twd")
            if isinstance(eps, bool) or not isinstance(eps, (int, float)) or not math.isfinite(eps):
                reasons.append("missing_or_nonfinite_eps")
        rows.append({"period": period, "status": "pending" if reasons else "verified_direct",
                     "reasons": reasons, "evidence": row or None})
    complete = all(r["status"] == "verified_direct" for r in rows)
    return {
        "stock_id": stock_id, "latest_verified_earnings_period": latest_reported,
        "quarters": rows, "verified_count": sum(r["status"] == "verified_direct" for r in rows),
        "ttm_reported_quarter_sum_twd": sum(r["evidence"]["eps_twd"] for r in rows) if complete else None,
        "ttm_status": "available_reported_quarterly_sum" if complete else "pending",
        "statutory_twelve_month_weighted_eps": None,
        "strict_historical_pit_verified": False,
        "annual_minus_ytd_eps_allowed": False,
    }


def vendor_history(records, cutoff):
    """Exact positive sample statistics, with exclusions and no vintage upgrade."""
    accepted, excluded, seen = [], [], set()
    for record in sorted(records, key=lambda r: r["data_date"]):
        day = record["data_date"]
        if day in seen:
            raise ValueError("Duplicate valuation date; explicitly select one revision")
        seen.add(day)
        value = record["values"].get("reported_pe")
        reason = None
        if day > str(cutoff)[:10] or timestamp(record["retrieved_at"]) > timestamp(cutoff):
            reason = "after_cutoff"
        elif not record.get("raw_sha256"):
            reason = "missing_source_identity"
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            reason = "missing_or_nonfinite_pe"
        elif value <= 0:
            reason = "nonpositive_pe"
        if reason:
            excluded.append({"date": day, "reason": reason})
        else:
            accepted.append({"date": day, "pe": value, "source_sha256": record["raw_sha256"],
                             "captured_at": record["retrieved_at"]})
    values = [r["pe"] for r in accepted]
    by_year = {}
    for row in accepted:
        by_year.setdefault(row["date"][:4], []).append(row["pe"])
    return {
        "status": "current_revision_retrospective_descriptive",
        "count": len(values), "first_date": accepted[0]["date"] if values else None,
        "last_date": accepted[-1]["date"] if values else None,
        "median": median(values) if values else None,
        "min": min(values) if values else None, "max": max(values) if values else None,
        "by_year": {year: {"count": len(v), "median": median(v)} for year, v in by_year.items()},
        "rows": accepted, "excluded": excluded, "daily_observations_are_independent": False,
        "diluted_denominator_verified": False, "strict_pit": False,
        "usable_as_forward_fair_multiple": False,
    }


def peer_basis(target, peer):
    """A financial peer gate, separate from a qualitative business-peer judgement."""
    reasons = []
    for key in ("price_date", "currency", "scope", "eps_basis", "forecast_start", "forecast_end"):
        if not target.get(key) or not peer.get(key) or target[key] != peer[key]:
            reasons.append(f"missing_or_mismatched_{key}")
    if target.get("eps_basis") != "forward_four_quarter_diluted":
        reasons.append("not_forward_four_quarter_diluted")
    for label, row in (("target", target), ("peer", peer)):
        if not row.get("denominator_verified") or not row.get("source_sha256"):
            reasons.append(f"{label}_denominator_unverified")
    return {"status": "comparable_basis" if not reasons else "not_eligible_for_forward_multiple",
            "reasons": reasons, "business_comparability_separately_required": True,
            "fair_multiple_certified": False}


def single_quarter_reconciliation(vendor_values, reviewed_quarter):
    """Compare amounts to a reviewed original. Never difference EPS or guess YTD."""
    fields = {"Revenue": "revenue_twd", "OperatingIncome": "operating_profit_twd",
              "EquityAttributableToOwnersOfParent": "parent_profit_twd"}
    differences = []
    for vendor_key, official_key in fields.items():
        vendor = vendor_values.get(vendor_key)
        official = reviewed_quarter.get(official_key)
        valid = all(not isinstance(x, bool) and isinstance(x, (int, float))
                    and math.isfinite(x) for x in (vendor, official))
        differences.append({"field": official_key, "vendor_twd": vendor,
                            "official_single_quarter_twd": official,
                            "matches": abs(vendor - official) <= 1 if valid else False})
    return {"status": "matched_reviewed_single_quarter" if all(r["matches"] for r in differences)
            else "not_eligible_as_verified_single_quarter",
            "fields": differences, "eps_differenced": False,
            "automatic_ytd_reclassification": False}
