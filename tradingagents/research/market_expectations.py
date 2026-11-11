"""Keep reported multiples, sourced forward EPS and reverse assumptions distinct."""

from decimal import Decimal, InvalidOperation
from re import fullmatch
from statistics import median

from .review import _end_of_day
from .scenarios import finite, timestamp


def quantile(values, fraction):
    values = sorted(values)
    if not values:
        return None
    i = (len(values) - 1) * fraction
    low = int(i)
    return values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (i - low)


def valuation(
    price,
    reported_rows,
    cutoff,
    forward=None,
    trailing=None,
    reverse_pe=None,
    valuation_config=None,
    previous_sample=None,
):
    """Reported PE has an unverified vendor denominator; never relabel it TTM."""
    price = finite(price)
    if price <= 0:
        raise ValueError("Positive unadjusted price required")
    from .valuation_basis import compare, sample

    receipt = sample(reported_rows, cutoff, valuation_config)
    rows = [(r["date"], r["pe"]) for r in receipt["rows"]]
    pe_values = [v for _, v in rows]
    result = {
        "price_twd": price,
        "sample_receipt": receipt,
        "version_comparison": compare(previous_sample, receipt)
        if previous_sample
        else {"status": "no_prior_sample_supplied"},
        "reported_history": {
            "status": "available_descriptive" if rows else "unavailable",
            "count": len(rows),
            "first_date": receipt["first_date"],
            "last_date": receipt["last_date"],
            "sample_sha256": receipt["sample_sha256"],
            "latest": rows[-1][1] if rows else None,
            "median": median(pe_values) if rows else None,
            "p25": quantile(pe_values, 0.25),
            "p75": quantile(pe_values, 0.75),
            "basis": "vendor_reported_denominator_unverified_not_historical_forward_pe",
        },
        "forward": {
            "status": "unavailable",
            "reason": "Sourced 12-month diluted estimate required",
        },
        "trailing": {
            "status": "unavailable",
            "reason": "Verified comparable diluted trailing numerator/weighted-share basis required",
        },
        "reverse": [],
        "fair_value_established": False,
        "market_consensus_verified": False,
    }
    for key, packet in (("forward", forward), ("trailing", trailing)):
        if not packet:
            continue
        expected = (
            "next_12_month_diluted_estimate"
            if key == "forward"
            else "trailing_12_month_diluted_verified"
        )
        if (
            packet.get("basis") != expected
            or not packet.get("source_sha256")
            or not packet.get("basis_reviewed")
        ):
            raise ValueError("EPS denominator and source must be explicitly reviewed")
        if max(timestamp(packet["published_at"]), timestamp(packet["captured_at"])) > timestamp(
            cutoff
        ):
            continue
        eps = finite(packet["eps_twd"])
        if eps > 0:
            result[key] = {
                "status": "conditional_estimate" if key == "forward" else "available",
                "eps_twd": eps,
                "pe": price / eps,
                "source": packet,
                "confidence_interval": None,
            }
    # History quantiles are transparent sensitivity axes, not validated exit multiples.
    axes = (
        reverse_pe
        if reverse_pe is not None
        else sorted(
            {
                x
                for x in (
                    quantile(pe_values, 0.25),
                    quantile(pe_values, 0.5),
                    quantile(pe_values, 0.75),
                )
                if x
            }
        )
    )
    reference_eps = result["forward"].get("eps_twd") or result["trailing"].get("eps_twd")
    reference_basis = (
        "next_12_month_diluted_estimate"
        if result["forward"].get("eps_twd")
        else "trailing_12_month_diluted_verified"
        if reference_eps
        else None
    )
    for pe in axes:
        pe = finite(pe)
        if pe <= 0:
            raise ValueError("Positive reverse valuation PE required")
        required = price / pe
        result["reverse"].append(
            {
                "assumed_pe": pe,
                "required_eps_twd": required,
                "required_growth_vs_reference_pct": (required / reference_eps - 1) * 100
                if reference_eps
                else None,
                "reference_basis": reference_basis,
                "required_growth_vs_trailing_pct": (required / result["trailing"]["eps_twd"] - 1)
                * 100
                if result["trailing"].get("eps_twd")
                else None,
                "required_change_vs_forward_estimate_pct": (
                    required / result["forward"]["eps_twd"] - 1
                )
                * 100
                if result["forward"].get("eps_twd")
                else None,
                "label": "conditional_reverse_equation_not_observed_market_expectation",
                "axis_source": "explicit_assumption"
                if reverse_pe is not None
                else "vendor_reported_history_quantiles_sensitivity_only",
            }
        )
    return result


def _number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("Invalid expectation value") from error
    if not result.is_finite():
        raise ValueError("Non-finite expectation value")
    return result


def _timed(value):
    # A calendar date cannot order two events on the same date.
    return timestamp(value) if isinstance(value, str) and len(value) > 10 else None


def _bound(row):
    return bool(row.get("version") and fullmatch(r"[0-9a-f]{64}", row.get("source_sha256") or ""))


def _match(actual, estimates, cutoff):
    if not _bound(actual):
        return "actual_source_unverified", None
    actual_available = _timed(actual.get("available_at"))
    if actual_available is None or actual_available > cutoff:
        return "actual_not_available_by_cutoff", None
    first = _timed(actual.get("first_published_at"))
    published = _timed(actual.get("published_at"))
    captured = _timed(actual.get("captured_at"))
    if first is None or published != first or captured is None or not first <= captured <= actual_available:
        return "actual_initial_release_time_unverified", None
    match_fields = ("metric", "period_start", "period_end", "basis", "scope", "unit")
    matches = [
        e for e in estimates
        if all(actual.get(field) and e.get(field) == actual[field] for field in match_fields)
        and e.get("source_kind") == "analyst_consensus"
        and e.get("statistic") == "median" and _bound(e)
    ]
    if not matches:
        return "no_comparable_consensus", None
    eligible = []
    for estimate in matches:
        published, captured = (_timed(estimate.get(k)) for k in ("published_at", "captured_at"))
        if published and captured and published <= captured < first:
            eligible.append((published, estimate))
    if not eligible:
        return "no_verified_pre_release_consensus", None
    latest = max(date for date, _ in eligible)
    latest_rows = [e for date, e in eligible if date == latest]
    if len({_number(e["value"]) for e in latest_rows}) != 1:
        return "conflicting_latest_consensus", None
    return None, min(latest_rows, key=lambda e: e["source_sha256"])


def compare_observations(stock, as_of, actuals, estimates):
    """Consume only source-normalized records. Never match annual to quarter or infer basis."""
    cutoff = _end_of_day(as_of)
    actuals = [a for a in actuals if a["stock_id"] == stock and a["source_kind"] == "official_actual"
               and a["period_end"] <= as_of]
    estimates = [e for e in estimates if e["stock_id"] == stock]
    metrics = {}
    for label, kinds in (("eps", {"basic_eps", "diluted_eps"}), ("revenue", {"revenue"})):
        related = [e for e in estimates if e["metric"] in kinds | {label} and e["source_kind"] == "analyst_consensus"]
        comparisons = []
        for actual in sorted((a for a in actuals if a["metric"] in kinds),
                             key=lambda a: (a["period_end"], a["period_start"], a["metric"]), reverse=True):
            reason, estimate = _match(actual, related, cutoff) if related else ("no_consensus_source", None)
            delta = change = None
            if estimate is not None:
                expected = _number(estimate["value"])
                delta = _number(actual["value"]) - expected
                change = str(delta / abs(expected) * 100) if expected else None
            comparisons.append({"status": "ready" if estimate else "unavailable", "reason": reason,
                                "actual": actual, "consensus": estimate, "difference": str(delta) if delta is not None else None,
                                "difference_pct": change})
        ready = sum(r["status"] == "ready" for r in comparisons)
        metrics[label] = {
            "status": ("ready" if ready == len(comparisons) and ready else
                       "limited" if ready or related else "unavailable"),
            "reason": (comparisons[0]["reason"] if comparisons else
                       "no_verified_actual" if related else "no_consensus_source"),
            "consensus_vintages": related, "comparisons": comparisons,
        }
    statuses = {metric["status"] for metric in metrics.values()}
    status = statuses.pop() if len(statuses) == 1 else "limited"
    return {"schema_version": 1, "stock_id": stock, "as_of": as_of, "status": status, "metrics": metrics,
            "policy": "Median consensus captured before the verified initial disclosure; exact issuer, period, scope, EPS basis and unit."}


REASON_LABELS = {
    "actual_source_unverified": "實績來源版本未驗證",
    "actual_not_available_by_cutoff": "截止日前可用性未驗證",
    "actual_initial_release_time_unverified": "實績首次公布時間未驗證",
    "no_comparable_consensus": "缺少期間、範圍及口徑一致的共識",
    "no_verified_pre_release_consensus": "缺少可證明在實績公布前可見的共識",
    "conflicting_latest_consensus": "同時發布的共識版本互相衝突",
    "no_consensus_source": "缺少可核對的市場共識來源",
    "no_verified_actual": "缺少可核對的實績",
}


def display_rows(packet):
    rows = []
    for key, label in (("eps", "EPS"), ("revenue", "營收")):
        metric = packet["metrics"][key]
        recent = next(iter(metric["comparisons"]), None)
        if recent and recent["status"] == "ready":
            text = f'{recent["difference"]} {recent["actual"]["unit"]}'
        else:
            text = REASON_LABELS.get(metric["reason"], "本期沒有可用比較")
        rows.append([label, metric["status"], text])
    return rows
