"""Source-reviewed, field-level statement periods. Unknown is never a quarter."""

import base64
import calendar
import copy
import hashlib
import json
import math
from datetime import date, datetime, time

from .publications import TAIPEI, instant

AMOUNTS = {"Revenue", "GrossProfit", "CostOfGoodsSold", "OperatingExpenses", "OperatingIncome",
           "IncomeAfterTaxes", "EquityAttributableToOwnersOfParent", "NoncontrollingInterests",
           "PretaxProfit", "TaxExpense", "CashFlowsFromOperatingActivities",
           "PropertyAndPlantAndEquipment", "CashFlowsFromInvestingActivities",
           "CashFlowsFromFinancingActivities"}
EPS = {"DilutedEPS", "BasicEPS"}
SHARES = {"BasicWeightedShares", "DilutedWeightedShares"}
BALANCES = {"TotalAssets", "Liabilities", "CashAndCashEquivalents", "Equity"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def cutoff_time(value):
    return (datetime.combine(date.fromisoformat(value), time.max, TAIPEI)
            if len(value) == 10 else instant(value))


def unknown_fields(record):
    """Observation dates and vendor units are retained, without a period attestation."""
    return {key: {"period_start": None, "period_end": None, "data_date": record.get("data_date"),
                  "vendor_period_label": record["period"], "basis": "unknown",
                  "scope": "unknown", "unit": "TWD/share" if "EPS" in key else "TWD",
                  "statement_type": "unknown",
                  "unit_status": "vendor_declared_unverified",
                  "source_version": digest(record.get("raw", {})), "version_set": None,
                  "source": record.get("source"), "updated_at": record.get("retrieved_at"),
                  "published_at": record.get("published_at"), "reviewed": False}
            for key in record["values"]}


def _document(doc, stock, cutoff):
    if doc.get("stock_id") != stock or not doc.get("revision_id"):
        raise ValueError("Financial source company/revision mismatch")
    if not doc.get("source_url", "").startswith("https://"):
        raise ValueError("Financial source URL required")
    raw = base64.b64decode(doc["content_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != doc.get("sha256"):
        raise ValueError("Financial original bytes changed")
    captured = doc.get("captured_at")
    if not captured or len(captured) <= 10 or instant(captured) > cutoff:
        raise ValueError("Financial original captured after cutoff or timestamp missing")
    published = doc.get("published_at")
    if published:
        if cutoff_time(published) > cutoff:
            raise ValueError("Financial original published after cutoff")
    elif not (doc.get("public_capture_verified") is True
              and doc.get("publication_time_status") == "unknown_current_revision"):
        raise ValueError("Financial publication/public capture proof missing")


def resolve(review, stock, cutoff):
    """Resolve reviewed observations; only whitelisted amounts can be differenced.

    Document hashes verify bytes, not transcription semantics. A named source
    reviewer must attest the original column, values and version bridge.
    """
    if not review:
        return {"rows": [], "rejected": [], "status": "unavailable_no_period_review"}
    if review.get("stock_id") != stock:
        raise ValueError("Financial review company mismatch")
    cutoff = cutoff_time(cutoff)
    docs = review.get("documents", {})
    superseded = superseded_documents(docs, stock, cutoff)
    rows, rejected, observations = [], [], {}
    for obs in review.get("observations", []):
        identifier = obs["id"]
        if identifier in observations:
            raise ValueError("Duplicate financial observation ID")
        observations[identifier] = obs
        reason = None
        try:
            field, basis = obs["field"], obs["basis"]
            if field not in AMOUNTS | EPS | SHARES | BALANCES:
                raise ValueError("Unsupported accounting field")
            if obs.get("stock_id") != stock or obs.get("scope") != "consolidated":
                raise ValueError("Consolidated company-specific review required")
            unit = "TWD/share" if field in EPS else "shares" if field in SHARES else "TWD"
            if obs.get("unit") != unit or not obs.get("version_set"):
                raise ValueError("Unit/version set missing")
            statement = obs.get("statement_type")
            allowed = ({"balance"} if field in BALANCES else {"eps_note", "income"}
                       if field in EPS | SHARES else {"cash_flow"}
                       if field in {"CashFlowsFromOperatingActivities", "PropertyAndPlantAndEquipment",
                                    "CashFlowsFromInvestingActivities", "CashFlowsFromFinancingActivities"}
                       else {"income"})
            if statement not in allowed:
                raise ValueError("Statement type missing or balance/flow mismatch")
            value = obs["value"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Non-finite financial amount")
            if not obs.get("reviewed_by") or not obs.get("locator") or obs.get("values_reviewed") is not True:
                raise ValueError("Original numeric transcription not reviewed")
            end = date.fromisoformat(obs["period_end"])
            if end > cutoff.astimezone(TAIPEI).date():
                raise ValueError("Future accounting period")
            if basis == "instant":
                if field not in BALANCES or obs.get("period_start") is not None:
                    raise ValueError("Point balance cannot become a period amount")
            else:
                if field in BALANCES or basis not in {"single_quarter", "ytd"}:
                    raise ValueError("Unsupported period basis")
                if end.month not in {3, 6, 9, 12} or end.day != calendar.monthrange(end.year, end.month)[1]:
                    raise ValueError("Quarter end required")
                start = date.fromisoformat(obs["period_start"])
                expected = date(end.year, 1 if basis == "ytd" else end.month - 2, 1)
                if start != expected:
                    raise ValueError("Period start/basis mismatch")
                if field in EPS and obs.get("eps_kind") != ("diluted" if field == "DilutedEPS" else "basic"):
                    raise ValueError("Basic/diluted EPS mismatch")
            refs = obs.get("document_ids", [])
            if not refs or len(refs) != len(set(refs)):
                raise ValueError("Distinct original references required")
            for key in refs:
                _document(docs[key], stock, cutoff)
                if key in superseded:
                    raise ValueError("Selected original superseded by a known correction; review replacement and recompute")
            known_dates = [cutoff_time(docs[k].get("published_at") or docs[k]["captured_at"])
                           for k in refs]
            if max(known_dates).astimezone(TAIPEI).date() < end:
                raise ValueError("Pre-period forecast document cannot certify an actual quarter")
        except (ValueError, KeyError, TypeError) as exc:
            reason = str(exc)
        if reason:
            rejected.append({"observation_id": identifier, "reason": reason})
            continue
        rows.append({**obs, "source_versions": [docs[k]["revision_id"] for k in refs],
                     "source_urls": [docs[k]["source_url"] for k in refs],
                     "source_basis": basis,
                     "source_sha256s": [docs[k]["sha256"] for k in refs],
                     "published_at": [docs[k].get("published_at") for k in refs],
                     "updated_at": max((docs[k]["captured_at"] for k in refs), key=instant),
                     "method": "reviewed_direct_observation", "input_ids": [identifier]})
    valid = {r["id"]: r for r in rows}
    resolved = [r for r in rows if r["basis"] == "single_quarter"
                or (r["basis"] == "ytd" and r["field"] in AMOUNTS and r["period_end"][5:] == "03-31")]
    for current in rows:
        if current["basis"] != "ytd" or current["field"] not in AMOUNTS:
            continue  # EPS, weighted shares and point balances are never differenced.
        end = date.fromisoformat(current["period_end"])
        if end.month == 3:
            continue
        previous_end = date(end.year, end.month - 3, calendar.monthrange(end.year, end.month - 3)[1]).isoformat()
        candidates = [r for r in rows if r["basis"] == "ytd" and r["field"] == current["field"]
                      and r["period_end"] == previous_end]
        if len(candidates) != 1:
            rejected.append({"observation_id": current["id"], "reason": "adjacent_same_year_YTD_missing_or_ambiguous"})
            continue
        previous = candidates[0]
        compatible = all(current[k] == previous[k] for k in ("stock_id", "scope", "unit", "version_set", "period_start", "statement_type"))
        bridges = [b for b in review.get("bridges", [])
                   if b.get("current_id") == current["id"] and b.get("previous_id") == previous["id"]]
        bridge = bridges[0] if len(bridges) == 1 else {}
        if (not compatible or bridge.get("bridge_verified") is not True
                or not bridge.get("reviewed_by") or not bridge.get("locator")
                or bridge.get("field") != current["field"]
                or bridge.get("version_set") != current["version_set"]
                or set(bridge.get("document_sha256s", [])) != set(current["source_sha256s"] + previous["source_sha256s"])):
            rejected.append({"observation_id": current["id"], "reason": "version_scope_unit_or_explicit_bridge_unverified"})
            continue
        assert current["id"] in valid and previous["id"] in valid
        resolved.append({**current, "basis": "single_quarter",
                         "period_start": date(end.year, end.month - 2, 1).isoformat(),
                         "value": current["value"] - previous["value"],
                         "method": "same_version_adjacent_ytd_difference",
                         "input_ids": [current["id"], previous["id"]], "bridge": bridge,
                         "source_sha256s": sorted(set(current["source_sha256s"] + previous["source_sha256s"])),
                         "source_versions": current["source_versions"] + previous["source_versions"],
                         "updated_at": max((current["updated_at"], previous["updated_at"]), key=instant)})
    grouped = {}
    for row in resolved:
        row = {**row, "basis": "single_quarter"}
        grouped.setdefault((row["period_end"], row["field"]), []).append(row)
    selected = []
    for candidates in grouped.values():
        signature = {(r["value"], r["period_start"], r["scope"], r["unit"], r["version_set"]) for r in candidates}
        if len(signature) != 1:
            rejected.append({"observation_id": [r["id"] for r in candidates], "reason": "conflicting_selected_values_or_versions"})
        else:
            selected.append(next((r for r in candidates if r["method"] == "reviewed_direct_observation"), candidates[0]))
    return {"rows": selected, "rejected": rejected,
            "status": "partial" if rejected else "reviewed", "eps_or_share_differencing": False,
            "superseded_documents": superseded}


def superseded_documents(documents, stock, cutoff):
    """A captured correction invalidates its predecessor, never a future correction.

    The successor must bind eligible original bytes. This conservative invalidation
    does not certify replacement numbers or historical initial-version completeness.
    """
    cutoff = cutoff_time(cutoff) if isinstance(cutoff, str) else cutoff
    result = {}
    for key, doc in documents.items():
        parent = doc.get("supersedes")
        if parent not in documents or parent == key:
            continue
        try:
            _document(doc, stock, cutoff)
        except (KeyError, TypeError, ValueError):
            continue
        prior = documents[parent]
        if (prior.get("stock_id") == stock and prior.get("sha256") != doc.get("sha256")
                and cutoff_time(prior.get("published_at") or prior["captured_at"])
                < cutoff_time(doc.get("published_at") or doc["captured_at"])):
            result[parent] = key
    return result


def refresh_snapshot(snapshot, review=None):
    """Rebuild caches, including imported legacy snapshots; never overwrite raw rows."""
    from .calculations import financial_metrics
    from .provider import FINANCIAL

    result = copy.deepcopy(snapshot)
    if review is not None:
        result["financial_period_review"] = copy.deepcopy(review)
    for group in (result.get("datasets", {}), result.get("extensions", {})):
        for dataset in ("TaiwanStockFinancialStatements", "TaiwanStockCashFlowsStatement", "TaiwanStockBalanceSheet"):
            for record in group.get(dataset, {}).get("records", []):
                record["field_periods"] = unknown_fields(record)
    result["quarterly_financials"] = financial_metrics(
        result["datasets"].get(FINANCIAL, {}).get("records", []),
        result.get("financial_period_review"), result["as_of"], result["instrument"]["stock_id"])
    result["financial_period_policy"] = "reviewed_single_quarter_only_v1"
    result["financial_period_audit"] = resolve(result.get("financial_period_review"),
                                               result["instrument"]["stock_id"], result["as_of"])
    if "extensions" in result:
        from .extensions import extension_metrics

        result["extended_research"] = extension_metrics(result)
    return result


def cash_metrics(records, review, cutoff, stock):
    result = resolve(review, stock, cutoff)
    periods = {r["period"]: {} for r in records}
    for row in result["rows"]:
        periods.setdefault(row["period_end"], {})[row["field"]] = row

    def pair(values, a, b):
        x, y = values.get(a), values.get(b)
        return bool(x and y) and all(x[k] == y[k] for k in
                                    ("period_start", "period_end", "scope", "version_set", "unit"))

    output = []
    for period, fields in sorted(periods.items()):
        cfo = fields.get("CashFlowsFromOperatingActivities", {}).get("value")
        ppe = fields.get("PropertyAndPlantAndEquipment", {}).get("value")
        cash_pair = pair(fields, "CashFlowsFromOperatingActivities", "PropertyAndPlantAndEquipment")
        ratios = {}
        for name, denominator in (("cfo_to_revenue", "Revenue"),
                                  ("cfo_to_parent_profit", "EquityAttributableToOwnersOfParent")):
            matched = pair(fields, "CashFlowsFromOperatingActivities", denominator)
            value = fields.get(denominator, {}).get("value")
            ratios[name] = cfo / value if matched and value and value > 0 else None
        output.append({"period": period, "operating_twd_as_reported": cfo,
                       "ppe_cash_outflow_twd_as_reported": -ppe if ppe is not None and ppe <= 0 else None,
                       "operating_less_ppe_twd_as_reported": cfo + ppe
                       if cash_pair and ppe <= 0 else None,
                       "basis": "source statement period reviewed to single quarter; unknown periods unavailable",
                       "field_periods": fields, **ratios})
    return output
