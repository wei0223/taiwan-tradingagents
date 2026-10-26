"""Immutable local release ledger. Issue time is real, never the historical input date."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from tradingagents.dataflows.taiwan.publications import TAIPEI

from .bundle import canonical, fingerprint
from .scenarios import forecast, timestamp


def quarter_end(period):
    year, quarter = period.split("-Q")
    return date(int(year), int(quarter) * 3, {1: 31, 2: 30, 3: 30, 4: 31}[int(quarter)])


def publish(root, bundle, specification, debate, model_version, clock=None):
    issued = (clock or (lambda: datetime.now(UTC)))()
    if issued.tzinfo is None:
        raise ValueError("Release clock requires timezone")
    issued = issued.astimezone(UTC)
    model = forecast(bundle, specification)
    if model["status"] != "ready":
        raise ValueError("Cannot seal an unavailable forecast")
    if timestamp(specification["created_at"]) > issued:
        raise ValueError("Release precedes model creation")
    if (
        date.fromisoformat(bundle["as_of"])
        > issued.astimezone(timestamp(bundle["as_of"]).tzinfo).date()
    ):
        raise ValueError("Release cutoff is in the future")
    if not debate.get("complete") or debate.get("bundle_sha256") != fingerprint(bundle):
        raise ValueError("Completed source-matched debate required")
    if not model_version:
        raise ValueError("Version required")
    predictions = []
    for scenario, result in model["scenarios"].items():
        for row in result["quarters"]:
            if quarter_end(row["period"]) <= issued.astimezone(TAIPEI).date():
                raise ValueError("Cannot label an ended quarter as prospective")
            for metric, value, basis in [
                ("eps", row["eps_twd"], "diluted_single_quarter"),
                ("revenue", row["revenue_twd"], "single_quarter_twd"),
            ]:
                predictions.append(
                    {
                        "stock_id": bundle["stock_id"],
                        "period": row["period"],
                        "metric": metric,
                        "basis": basis,
                        "value": value,
                        "scenario": scenario,
                        "model_version": model_version + ":" + scenario,
                        "issued_at": issued.isoformat(),
                        "input_snapshot_sha256": fingerprint(bundle),
                        "input_hash_kind": "evidence_bundle_sha256_not_raw_snapshot",
                    }
                )
    receipt = {
        "schema_version": 1,
        "issued_at": issued.isoformat(),
        "release_kind": "local_research_delivery",
        "stock_id": bundle["stock_id"],
        "data_as_of": bundle["as_of"],
        "bundle_sha256": fingerprint(bundle),
        "specification_sha256": fingerprint(specification),
        "debate_sha256": fingerprint(debate),
        "model_version": model_version,
        "predictions": predictions,
        "forecast_status": "pending_future_actuals",
        "input_bundle": bundle,
        "specification": specification,
        "debate": debate,
        "limitations": [
            "Local timestamp and hash are not independent trusted timestamp notarization",
            "Raw captured inputs are never replaced by later restatements when evaluating original forecasts",
        ],
    }
    receipt["release_sha256"] = fingerprint(receipt)
    path = Path(root) / bundle["stock_id"] / (receipt["release_sha256"] + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical(receipt) + "\n")
    return path


def read_release(path):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        result["release_sha256"]
        != fingerprint({k: v for k, v in result.items() if k != "release_sha256"})
        or Path(path).stem != result["release_sha256"]
        or fingerprint(result["input_bundle"]) != result["bundle_sha256"]
        or fingerprint(result["specification"]) != result["specification_sha256"]
        or fingerprint(result["debate"]) != result["debate_sha256"]
    ):
        raise ValueError("Release ledger modified")
    if result.get("schema_version") == 2:
        from .prospective import verify_release

        verify_release(result, path)
    return result
