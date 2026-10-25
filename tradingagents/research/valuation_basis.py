"""Versioned PE samples and telescoping, reproducible change attribution."""

from datetime import timedelta
from statistics import median

from .bundle import fingerprint
from .scenarios import finite, timestamp


def sample(rows, cutoff, config=None):
    config = dict(config or {})
    if set(config) - {"start", "end"}:
        raise ValueError(
            "Vendor PE sampling supports only explicit start/end; verified bases use historical_ttm"
        )
    definition = {
        "metric": "vendor_reported_pe",
        "price_basis": "vendor_unverified",
        "eps_basis": "vendor_unverified_not_ttm_or_forward",
        "adjustment_basis": "vendor_current_revision_unverified",
        "start": config.get("start", "1900-01-01"),
        "end": config.get("end", str(cutoff)[:10]),
        "missing_rule": "exclude_null_with_reason",
        "outlier_rule": "keep_all_finite_positive_no_winsorization",
        "revision_rule": "one_captured_revision_per_date",
    }
    if definition["start"] > definition["end"]:
        raise ValueError("Reversed valuation sample window")
    accepted, excluded, dates = [], [], set()
    for row in rows:
        day = row["date"]
        if day in dates:
            raise ValueError("Duplicate PE sample date")
        dates.add(day)
        if not row.get("source_sha256"):
            raise ValueError("PE sample source required")
        reason = None
        if not definition["start"] <= day <= definition["end"]:
            reason = "outside_fixed_window"
        elif max(timestamp(day), timestamp(row["captured_at"])) > timestamp(cutoff):
            reason = "not_available_by_data_as_of"
        elif row.get("reported_pe") is None:
            reason = "missing_pe"
        else:
            value = finite(row["reported_pe"])
            if value <= 0:
                reason = "nonpositive_pe"
        if reason:
            excluded.append({"date": day, "reason": reason})
        else:
            accepted.append(
                {
                    "date": day,
                    "pe": value,
                    "source_sha256": row["source_sha256"],
                    "captured_at": row["captured_at"],
                    "eps_published_at": None,
                    "denominator_verified": False,
                }
            )
    accepted.sort(key=lambda x: x["date"])
    receipt = {
        "definition": definition,
        "rows": accepted,
        "excluded": sorted(excluded, key=lambda x: x["date"]),
    }
    return {
        **receipt,
        "sample_sha256": fingerprint(receipt),
        "definition_sha256": fingerprint(definition),
        "count": len(accepted),
        "first_date": accepted[0]["date"] if accepted else None,
        "last_date": accepted[-1]["date"] if accepted else None,
        "median": median(x["pe"] for x in accepted) if accepted else None,
        "usable_as_verified_pe": False,
    }


def compare(old, new, threshold=0.1):
    threshold = finite(threshold)
    if not 0 <= threshold <= 1:
        raise ValueError("Difference threshold must be a fraction")
    for receipt in (old, new):
        material = {k: receipt[k] for k in ("definition", "rows", "excluded")}
        values = [row["pe"] for row in receipt["rows"]]
        expected_median = median(values) if values else None
        if (
            fingerprint(material) != receipt["sample_sha256"]
            or fingerprint(receipt["definition"]) != receipt["definition_sha256"]
            or expected_median != receipt["median"]
        ):
            raise ValueError("PE receipt changed; do not trust cached median or sample identity")
    a, b = ({r["date"]: r["pe"] for r in s["rows"]} for s in (old, new))
    common = sorted(a.keys() & b.keys())
    changed = [
        {"date": d, "old": a[d], "new": b[d], "delta": b[d] - a[d]} for d in common if a[d] != b[d]
    ]
    ma, mb = old["median"], new["median"]
    mid_a = median(a[d] for d in common) if common else None
    mid_b = median(b[d] for d in common) if common else None
    path = None
    if all(v is not None for v in (ma, mb, mid_a, mid_b)):
        path = {
            "removed_dates_effect": mid_a - ma,
            "common_date_revision_effect": mid_b - mid_a,
            "added_dates_effect": mb - mid_b,
            "total_change": mb - ma,
            "method": "ordered_telescoping_old_to_common_old_to_common_new_to_new_not_causal",
        }
    fraction = mb / ma - 1 if ma and mb is not None else None
    return {
        "old_sample_sha256": old["sample_sha256"],
        "new_sample_sha256": new["sample_sha256"],
        "same_definition": old["definition_sha256"] == new["definition_sha256"],
        "definition_changes": {
            k: [old["definition"].get(k), new["definition"].get(k)]
            for k in set(old["definition"]) | set(new["definition"])
            if old["definition"].get(k) != new["definition"].get(k)
        },
        "common_count": len(common),
        "added_dates": sorted(b.keys() - a.keys()),
        "removed_dates": sorted(a.keys() - b.keys()),
        "revised_common_dates": changed,
        "median_change_fraction": fraction,
        "review_required": fraction is None
        or abs(fraction) >= threshold
        or old["definition_sha256"] != new["definition_sha256"],
        "attribution": path,
        "denominator_verification": "vendor_denominator_still_unverified",
    }


def historical_ttm(prices, eps_packets, config):
    """Use exact-day unadjusted prices and previously captured verified TTM packets.

    No summing basic/diluted quarterly EPS or accepting a current backfill as PIT.
    Numerator/denominator must be a separately reconciled same-period TTM packet.
    """
    if (
        config.get("price_basis") != "unadjusted"
        or config.get("eps_basis") != "verified_ttm_diluted"
    ):
        raise ValueError(
            "Explicit matched unadjusted price and verified diluted TTM basis required"
        )
    rows, excluded = [], []
    for price in sorted(prices, key=lambda r: r["date"]):
        day = price["date"]
        if not config["start"] <= day <= config["end"]:
            continue
        cutoff = timestamp(day) + timedelta(days=1) - timedelta(microseconds=1)
        if not price.get("captured_at") or timestamp(price["captured_at"]) > cutoff:
            excluded.append({"date": day, "reason": "price_vintage_not_contemporaneously_captured"})
            continue
        known = [
            p
            for p in eps_packets
            if p.get("basis") == "trailing_12_month_diluted_verified"
            and p.get("basis_reviewed")
            and p.get("source_sha256")
            and p.get("locator")
            and p.get("stock_id") == price.get("stock_id")
            and p.get("currency") == "TWD"
            and p.get("price_adjustment_basis") == "unadjusted"
            and timestamp(p["published_at"]) <= cutoff
            and timestamp(p["captured_at"]) <= cutoff
            and p["period_end"] <= day
        ]
        if not known or not price.get("source_sha256"):
            excluded.append({"date": day, "reason": "no_contemporaneously_captured_matched_ttm"})
            continue
        selected = max(known, key=lambda p: (p["period_end"], p["published_at"], p["captured_at"]))
        if finite(selected["diluted_weighted_shares"]) <= 0:
            raise ValueError("Positive diluted shares required")
        eps = finite(selected["diluted_numerator_twd"]) / finite(
            selected["diluted_weighted_shares"]
        )
        if selected["diluted_weighted_shares"] <= 0 or eps <= 0 or finite(price["close"]) <= 0:
            raise ValueError("Positive verified TTM and price required")
        rows.append(
            {
                "date": day,
                "pe": price["close"] / eps,
                "eps_twd": eps,
                "eps_source_sha256": selected["source_sha256"],
                "eps_published_at": selected["published_at"],
                "price_source_sha256": price["source_sha256"],
            }
        )
    return {
        "definition": config,
        "rows": rows,
        "excluded": excluded,
        "median": median(r["pe"] for r in rows) if rows else None,
        "count": len(rows),
        "sample_sha256": fingerprint({"config": config, "rows": rows, "excluded": excluded}),
    }
