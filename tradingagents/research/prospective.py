"""Explicitly selected research forecasts; conditional scenarios never become scores."""

import base64
import hashlib
import math
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from tradingagents.dataflows.taiwan.publications import TAIPEI

from .bundle import canonical, fingerprint
from .financial_model import waterfall
from .knowledge import exact, source_eligibility
from .publication import quarter_end
from .scenarios import next_quarters

STAGES = ("bull_opening", "bear_opening", "bull_reply", "bear_reply", "risk")


def calculate(bundle, model, documents, issued):
    stock, cutoff = bundle["stock_id"], exact(bundle["knowledge_cutoff_at"])
    if not re.fullmatch(r"\d{4,6}", stock):
        raise ValueError("Explicit Taiwan stock identifier required")
    if cutoff > issued or exact(model["created_at"]) > issued:
        raise ValueError("Publication precedes source/model cutoff")
    if bundle["as_of"] != cutoff.astimezone(TAIPEI).date().isoformat():
        raise ValueError("Bundle day must match exact Taipei knowledge cutoff")
    targets = next_quarters(bundle["as_of"])
    if [q["period"] for q in model["quarters"]] != targets:
        raise ValueError("Exactly four consecutive forward quarters required")
    if not model.get("model_version") or model.get("stock_id") != stock:
        raise ValueError("Company model/version required")
    ctx = {"cutoff_at": cutoff.isoformat(), "mode": "latest_research"}
    for doc in documents.values():
        raw = base64.b64decode(doc["content_base64"], validate=True)
        url = urlsplit(doc["source_url"])
        if (not raw or hashlib.sha256(raw).hexdigest() != doc["sha256"]
                or doc.get("stock_id") != stock or url.scheme != "https"
                or not url.hostname or url.username or url.password
                or not source_eligibility(doc, ctx)["eligible"]):
            raise ValueError("Unavailable/mismatched publication original")
    result = []
    for quarter in model["quarters"]:
        if quarter_end(quarter["period"]) <= issued.astimezone(TAIPEI).date():
            raise ValueError("An ended quarter cannot be a prospective forecast")
        values, review = quarter["inputs"], quarter["parameter_review"]
        if set(values) != set(review):
            raise ValueError("Every model parameter requires a review")
        for entry in review.values():
            if (entry.get("kind") != "assumption" or not entry.get("rationale")
                    or not entry.get("uncertainty") or not entry.get("source_ids")
                    or not set(entry["source_ids"]) <= set(documents)):
                raise ValueError("Future inputs need explicit source-anchored assumptions")
        result.append({"period": quarter["period"], **waterfall(values)})
    return result


def selected_predictions(bundle, model, calculations, selections, issued):
    expected = {(q["period"], metric) for q in calculations for metric in ("eps", "revenue")}
    if {(s["period"], s["metric"]) for s in selections} != expected or len(selections) != 8:
        raise ValueError("Explicit selection for every quarter and metric required")
    predictions = []
    for row in selections:
        if row["decision"] not in {"publish_forecast", "conditional_only", "unavailable"}:
            raise ValueError("Unknown publication decision")
        if not row.get("reviewed_by") or not row.get("reason"):
            raise ValueError("Named publication ruling required")
        if row["decision"] != "publish_forecast":
            continue
        if row.get("uncalibrated_acknowledged") is not True:
            raise ValueError("Uncalibrated research status must be acknowledged")
        if row["metric"] == "eps" and not all(row.get(k) for k in (
                "denominator_assumption_review", "anti_dilution_policy", "numerator_policy")):
            raise ValueError("EPS forecast requires explicit denominator/anti-dilution/numerator review")
        calculated = next(q for q in calculations if q["period"] == row["period"])
        eps = row["metric"] == "eps"
        value = round(calculated["eps_twd"], 2) if eps else round(calculated["revenue_twd"] / 1e6) * 1e6
        if not math.isfinite(value):
            raise ValueError("Nonfinite forecast")
        predictions.append({"stock_id": bundle["stock_id"], "period": row["period"],
            "metric": row["metric"], "basis": "diluted_single_quarter" if eps else "single_quarter_twd",
            "value": value, "scenario": "selected_research_forecast", "model_version": model["model_version"],
            "issued_at": issued.isoformat(), "input_snapshot_sha256": fingerprint(bundle),
            "input_hash_kind": "evidence_bundle_sha256_not_raw_snapshot",
            "rounding": "0.01_TWD_per_share" if eps else "nearest_1000000_TWD",
            "not_company_guidance_or_consensus": True, "probability_calibrated": False})
    if not predictions:
        raise ValueError("No approved forecast: retain a review edition, do not issue an empty release")
    return predictions


def validate_debate(debate, bundle, issued):
    if debate.get("facts_sha256") != bundle["facts_packet_sha256"] or not debate.get("complete"):
        raise ValueError("Completed facts-matched independent debate required")
    stages = debate["stages"]
    if set(stages) != set(STAGES):
        raise ValueError("Both independent openings, replies and risk ruling required")
    roles = [stages[s].get("agent_id") for s in ("bull_opening", "bear_opening", "risk")]
    if not all(roles) or len(set(roles)) != 3:
        raise ValueError("Distinct bull/bear/risk identities required")
    for name, stage in stages.items():
        if (not stage.get("artifact_sha256") or not stage.get("content")
                or fingerprint(stage["content"]) != stage["artifact_sha256"]
                or stage["content"].get("stock_id") != bundle["stock_id"]
                or exact(stage["completed_at"]) > issued):
            raise ValueError("Debate artifact/time binding invalid")
        if name.endswith("opening") and stage["content"].get("facts_sha256") not in {
                bundle["facts_packet_sha256"], bundle.get("facts_packet_internal_sha256", bundle["facts_packet_sha256"])}:
            raise ValueError("Independent opening used a different facts packet")
        if name.endswith("reply") and not stage["content"].get("cross_examination"):
            raise ValueError("Cross-examination cannot be an empty label")
    for role, opponent in (("bull", "bear"), ("bear", "bull")):
        opening, reply = stages[role + "_opening"], stages[role + "_reply"]
        if (reply["agent_id"] != opening["agent_id"]
                or exact(reply["completed_at"]) < max(exact(opening["completed_at"]), exact(stages[opponent + "_opening"]["completed_at"]))):
            raise ValueError("Reply precedes openings or changes identity")
    if exact(stages["risk"]["completed_at"]) < max(exact(stages[r]["completed_at"]) for r in ("bull_reply", "bear_reply")):
        raise ValueError("Risk ruling precedes cross-examination")


def publish(root, bundle, model, debate, selections, documents, clock=None):
    issued = (clock or (lambda: datetime.now(UTC)))()
    if issued.tzinfo is None:
        raise ValueError("Timezone-aware actual publication clock required")
    issued = issued.astimezone(UTC)
    validate_debate(debate, bundle, issued)
    validate_ruling(bundle, model, debate, selections)
    calculations = calculate(bundle, model, documents, issued)
    predictions = selected_predictions(bundle, model, calculations, selections, issued)
    folder = Path(root) / bundle["stock_id"]
    from .publication import read_release

    for previous in folder.glob("*.json"):
        if read_release(previous).get("model_version") == model["model_version"]:
            raise FileExistsError("Model version already issued; create a deliberate new version")
    sources = {}
    for key, doc in documents.items():
        raw = base64.b64decode(doc["content_base64"], validate=True)
        relative = "source-bytes/" + doc["sha256"] + (".pdf" if raw.startswith(b"%PDF") else ".bin")
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError("Existing publication source changed")
        else:
            with path.open("xb") as handle:
                handle.write(raw)
        sources[key] = {k: v for k, v in doc.items() if k != "content_base64"}
        sources[key]["file"] = relative
    record = {"schema_version": 2, "release_kind": "uncalibrated_research_forecast",
        "issued_at": issued.isoformat(), "stock_id": bundle["stock_id"], "data_as_of": bundle["as_of"],
        "bundle_sha256": fingerprint(bundle), "specification_sha256": fingerprint(model),
        "debate_sha256": fingerprint(debate), "input_bundle": bundle, "specification": model,
        "debate": debate, "predictions": predictions, "calculations": calculations,
        "publication_selections": selections, "sources": sources, "model_version": model["model_version"],
        "forecast_status": "pending_future_actuals", "formal_valuation_qualified": False,
        "historical_performance_qualified": False, "trade_signal_issued": False,
        "limitations": ["Actual local publication timestamp; not independent trusted-time notarization",
            "Only selected predictions are scored; scenarios and conditional EPS are excluded",
            "Future estimates are explicit uncalibrated hypotheses, not verified actuals or statistical confidence intervals",
            "Quarterly revenue means consolidated financial-statement revenue, not a monthly-revenue sum",
            "No fair-value multiple, trading signal, missing TTM quarter or historical vintage is certified by publication"]}
    record["release_sha256"] = fingerprint(record)
    path = folder / (record["release_sha256"] + ".json")
    with path.open("x", encoding="utf8", newline="\n") as handle:
        handle.write(canonical(record) + "\n")
    return path


def validate_ruling(bundle, model, debate, selections):
    ruling = debate["stages"]["risk"]["content"]
    if ruling.get("stock_id") != bundle["stock_id"]:
        raise ValueError("Ruling company mismatch")
    approved_inputs = {q["period"]: q["inputs"] for q in ruling["selected_model"]["quarters"]}
    if {q["period"]: q["inputs"] for q in model["quarters"]} != approved_inputs:
        raise ValueError("Publication model differs from adjudicated inputs")
    decisions = {(q["period"], metric): q[metric] for q in ruling["publication_decisions"]
                 for metric in ("revenue", "eps")}
    if {(s["period"], s["metric"]): s["decision"] for s in selections} != decisions:
        raise ValueError("Publication selections differ from risk ruling")


def verify_release(record, path):
    root = Path(path).parent.resolve()
    for doc in record["sources"].values():
        relative = Path(doc["file"])
        target = (root / relative).resolve()
        if relative.is_absolute() or not target.is_relative_to(root) or hashlib.sha256(target.read_bytes()).hexdigest() != doc["sha256"]:
            raise ValueError("Sealed publication source bytes changed or escaped")
    issued = exact(record["issued_at"])
    docs = {k: {**v, "content_base64": base64.b64encode((root / v["file"]).read_bytes()).decode()}
            for k, v in record["sources"].items()}
    calculations = calculate(record["input_bundle"], record["specification"], docs, issued)
    validate_debate(record["debate"], record["input_bundle"], issued)
    validate_ruling(record["input_bundle"], record["specification"], record["debate"], record["publication_selections"])
    if (calculations != record["calculations"] or selected_predictions(record["input_bundle"], record["specification"],
            calculations, record["publication_selections"], issued) != record["predictions"]
            or record.get("formal_valuation_qualified") is not False
            or record.get("historical_performance_qualified") is not False):
        raise ValueError("Sealed model/selection/qualification does not recompute")
