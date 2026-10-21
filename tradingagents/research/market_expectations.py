"""Keep reported multiples, sourced forward EPS and reverse assumptions distinct."""

from statistics import median

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
