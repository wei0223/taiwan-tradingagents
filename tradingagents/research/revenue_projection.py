"""Three fixed revenue equations and horizon-matched retrospective error weights.

No capture is backdated: present numeric revisions make this calibration a
diagnostic only. Overlapping monthly origins do not constitute independent trials.
"""

import math
from statistics import mean

from .bundle import fingerprint
from .predictive import month_index, month_name

METHODS = ("seasonal_naive", "log_trend", "monthly_momentum")


def predict(history, origin, horizon, method):
    target = origin + horizon
    if method == "seasonal_naive":
        return history[target - 12]
    if method == "monthly_momentum":
        growth = mean(history[i] / history[i - 12] for i in range(origin - 2, origin + 1))
        return history[target - 12] * growth
    if method != "log_trend":
        raise ValueError("Unknown revenue method")
    ys = [math.log(history[i]) for i in range(origin - 11, origin + 1)]
    center = mean(ys)
    slope = sum((i - 5.5) * (y - center) for i, y in enumerate(ys)) / 143
    return math.exp(center + slope * (horizon + 5.5))


def project(snapshot):
    rows = snapshot.get("monthly_revenue", [])
    if snapshot.get("as_of") and any(
        month_index(r["period"]) >= month_index(snapshot["as_of"][:7]) for r in rows
    ):
        raise ValueError("Unfinished/future revenue month cannot enter projection")
    history = {month_index(r["period"]): r["revenue_twd"] for r in rows}
    if len(history) != len(rows) or any(v <= 0 for v in history.values()):
        raise ValueError("One positive selected revenue vintage per month required")
    if len(history) < 36:
        return {"status": "unavailable", "reason": "At least 36 months required"}
    end, start = max(history), min(history)
    forecasts, diagnostics = [], {}
    for horizon in range(1, 13):
        common = []
        for origin in range(start + 23, end - horizon + 1):
            try:
                estimates = {m: predict(history, origin, horizon, m) for m in METHODS}
                actual = history[origin + horizon]
            except KeyError:
                continue
            common.append(
                {
                    "origin": month_name(origin),
                    "target": month_name(origin + horizon),
                    "estimates": estimates,
                    "actual": actual,
                    "errors": {m: abs(x - actual) for m, x in estimates.items()},
                }
            )
        mae = {m: mean(r["errors"][m] for r in common) if common else None for m in METHODS}
        if len(common) >= 12:
            inverse = {m: 1 / max(x, 1) for m, x in mae.items()}
            weights = {m: x / sum(inverse.values()) for m, x in inverse.items()}
        else:
            weights = dict.fromkeys(METHODS, 1 / 3)
        try:
            estimates = {m: predict(history, end, horizon, m) for m in METHODS}
        except KeyError:
            return {"status": "unavailable", "reason": "Missing consecutive/prior-year months"}
        forecasts.append(
            {
                "period": month_name(end + horizon),
                "methods": estimates,
                "weights": weights,
                "weighted_twd": sum(estimates[m] * weights[m] for m in METHODS),
                "method_min_twd": min(estimates.values()),
                "method_max_twd": max(estimates.values()),
            }
        )
        diagnostics[str(horizon)] = {
            "horizon_months": horizon,
            "paired_origins": len(common),
            "mae_twd": mae,
            "folds": common,
            "weights_fitted_at_current_run": True,
        }
    return {
        "status": "conditional_current_revision_projection",
        "source_snapshot_sha256": fingerprint(snapshot),
        "forecasts": forecasts,
        "calibration": diagnostics,
        "historical_mode": "retrospective_diagnostic",
        "strict_pit": False,
        "out_of_sample_performance": False,
        "independent_origins": False,
        "confidence_interval": None,
        "limitation": "Method min/max is sensitivity, not prediction interval; current-vintage error weights are not historical deployable weights",
    }
