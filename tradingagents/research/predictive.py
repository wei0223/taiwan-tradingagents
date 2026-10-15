"""Point-in-time revenue models and paired walk-forward errors, without fitted-in-hindsight weights."""

import math
from statistics import mean

from .scenarios import finite, timestamp

METHODS = ("log_trend", "monthly_momentum", "industry_growth")


def month_index(period):
    year, month = map(int, period.split("-"))
    if not 1 <= month <= 12:
        raise ValueError("Invalid month")
    return year * 12 + month - 1


def month_name(index):
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def available(rows, cutoff):
    result = {}
    for row in rows:
        if not row.get("source_sha256") or not row.get("revision_id"):
            raise ValueError("Source vintage and explicit revision required")
        if max(timestamp(row["published_at"]), timestamp(row["captured_at"])) > timestamp(cutoff):
            continue
        key = month_index(row["period"])
        if key in result:
            raise ValueError("Select one revision explicitly")
        if key >= timestamp(cutoff).year * 12 + timestamp(cutoff).month - 1:
            raise ValueError("Future revenue observation")
        if finite(row["value"]) <= 0:
            raise ValueError("Positive revenue required")
        result[key] = row
    return result


def estimate(rows, cutoff, targets, method, industry=None):
    known = available(rows, cutoff)
    indices = [month_index(t) for t in targets]
    if method not in METHODS or not indices or indices != sorted(set(indices)):
        raise ValueError("Known method and ordered target months required")
    if not known:
        return {"status": "unavailable", "reason": "No numeric vintage known at cutoff"}
    end = max(known)
    if min(indices) <= end:
        raise ValueError("Revenue forecast targets must follow last known revenue")
    try:
        if method == "log_trend":
            # 12 consecutive months; no claim that a single year identifies seasonality.
            ys = [math.log(known[i]["value"]) for i in range(end - 11, end + 1)]
            center = mean(ys)
            slope = sum((i - 5.5) * (y - center) for i, y in enumerate(ys)) / 143
            values = [math.exp(center + slope * (i - end + 5.5)) for i in indices]
            detail = {
                "monthly_log_slope": slope,
                "training_months": 12,
                "seasonality_identified": False,
            }
        elif method == "monthly_momentum":
            yoy = mean(known[i]["value"] / known[i - 12]["value"] for i in range(end - 2, end + 1))
            values = [known[i - 12]["value"] * yoy for i in indices]
            detail = {
                "last_three_month_mean_yoy_factor": yoy,
                "limitation": "Prior-year same month required; no recursive fabricated history",
            }
        else:
            if not industry or not industry.get("independently_sourced"):
                return {
                    "status": "unavailable",
                    "reason": "Independent industry forecast/exposure evidence missing",
                }
            for field in ("industry_growth", "exposure", "other_growth"):
                x = industry[field]
                if not x.get("source_url") or not x.get("source_sha256"):
                    raise ValueError("Industry driver source required")
                if max(timestamp(x["published_at"]), timestamp(x["captured_at"])) > timestamp(
                    cutoff
                ):
                    return {
                        "status": "unavailable",
                        "reason": "Industry driver unavailable at cutoff",
                    }
            g, exposure, other = (
                finite(industry[f]["value"])
                for f in ("industry_growth", "exposure", "other_growth")
            )
            if not 0 <= exposure <= 1 or min(g, other) <= -1:
                raise ValueError("Invalid industry driver")
            factor = 1 + exposure * g + (1 - exposure) * other
            values = [known[i - 12]["value"] * factor for i in indices]
            detail = {
                "weighted_growth_factor": factor,
                "limitation": "Fixed market share/price/FX assumptions; independently sourced is not independent statistical error",
            }
    except KeyError:
        return {"status": "unavailable", "reason": "Required consecutive/prior-year months missing"}
    return {
        "status": "available",
        "method": method,
        "cutoff": cutoff,
        "months": dict(zip(targets, values, strict=True)),
        "total": sum(values),
        "detail": detail,
        "kind": "unvalidated_model_projection",
    }


def walk_forward(rows, folds, evaluation_at, industry_by_cutoff=None):
    actuals = available(rows, evaluation_at)
    results, skipped = [], []
    for fold in folds:
        cutoff, targets = fold["cutoff"], fold["targets"]
        if timestamp(cutoff) >= timestamp(evaluation_at):
            raise ValueError("Evaluation must follow training cutoff")
        truth = [actuals.get(month_index(t)) for t in targets]
        if any(x is None for x in truth):
            skipped.append({**fold, "reason": "Actuals not yet captured/published"})
            continue
        if any(timestamp(x["published_at"]) <= timestamp(cutoff) for x in truth):
            raise ValueError("Target already public at training cutoff")
        forecasts = {
            method: estimate(rows, cutoff, targets, method, (industry_by_cutoff or {}).get(cutoff))
            for method in METHODS
        }
        known = available(rows, cutoff)
        try:
            naive = sum(known[month_index(t) - 12]["value"] for t in targets)
        except KeyError:
            naive = None
        actual = sum(x["value"] for x in truth)
        results.append(
            {
                "cutoff": cutoff,
                "targets": targets,
                "actual": actual,
                "forecasts": forecasts,
                "absolute_errors": {
                    k: abs(v["total"] - actual)
                    for k, v in forecasts.items()
                    if v["status"] == "available"
                },
                "seasonal_naive_absolute_error": None if naive is None else abs(naive - actual),
            }
        )
    return {
        "folds": results,
        "skipped": skipped,
        "evaluated_at": evaluation_at,
        "status": "available" if any(x["absolute_errors"] for x in results) else "unavailable",
        "interpretation": "Captured-vintage replay; not proof of historical live publication or strategy alpha",
    }


def calibrated_weights(validation, issued_at, min_folds=6):
    folds = validation["folds"]
    if timestamp(validation["evaluated_at"]) > timestamp(issued_at):
        raise ValueError("Validation outcomes unavailable at model issue time")
    if any(timestamp(f["cutoff"]) >= timestamp(issued_at) for f in folds):
        raise ValueError("Training fold reaches issue time")
    # Only common folds, same targets, same baseline. No method-specific cherry-picking.
    paired = [
        f
        for f in folds
        if set(f["absolute_errors"]) == set(METHODS)
        and f["seasonal_naive_absolute_error"] is not None
    ]
    if len(paired) < min_folds:
        return {
            "status": "unavailable",
            "weights": None,
            "paired_folds": len(paired),
            "reason": "Insufficient common historical vintages; no ad-hoc most-likely growth",
        }
    errors = {k: mean(f["absolute_errors"][k] for f in paired) for k in METHODS}
    inverse = {k: 1 / max(v, 1e-9) for k, v in errors.items()}
    return {
        "status": "calibration_only",
        "weights": {k: v / sum(inverse.values()) for k, v in inverse.items()},
        "mae": errors,
        "paired_folds": len(paired),
        "holdout_superiority_proven": False,
        "limitation": "Weights require a later untouched test window before claiming improvement",
    }
