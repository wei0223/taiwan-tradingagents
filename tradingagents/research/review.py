"""Source-bound financial review flags, separate from valuation and trading hypotheses.

Numeric transcription and version equivalence require a named human/source reviewer.
Hashes verify archived bytes, not the correctness of that review. No issuer thresholds.
"""

import base64
import calendar
import hashlib
from datetime import UTC, date, datetime, time

from tradingagents.dataflows.taiwan.publications import TAIPEI

from .bundle import fingerprint
from .scenarios import finite, timestamp

AXES = ("accounting_earnings", "cash_sustainability", "valuation", "short_term")


class Unavailable(ValueError):
    """A required fact is missing or was not available at the research cutoff."""


def _end_of_day(value):
    return datetime.combine(date.fromisoformat(value), time.max, TAIPEI)


def _period(packet, basis):
    start, end = (date.fromisoformat(packet[k]) for k in ("period_start", "period_end"))
    if end.month not in (3, 6, 9, 12) or end.day != calendar.monthrange(end.year, end.month)[1]:
        raise ValueError("Official quarter end required")
    expected = date(end.year, 1 if basis == "ytd" else end.month - 2, 1)
    if start != expected or packet.get("basis") != basis:
        raise Unavailable("financial_period_or_basis_mismatch")
    return start, end


def _packet(packet, stock, documents, cutoff, fields=(), allowed_scopes=("consolidated",)):
    if not packet:
        raise Unavailable("reviewed_packet_missing")
    if packet.get("stock_id") != stock:
        raise ValueError("Review company mismatch")
    if not packet.get("reviewed_by") or packet.get("values_reviewed") is not True:
        raise Unavailable("numeric_transcription_not_reviewed")
    if not packet.get("locator") or not packet.get("version_set"):
        raise Unavailable("page_locator_and_selected_version_required")
    if packet.get("unit") != "TWD_and_shares" or packet.get("scope") not in allowed_scopes:
        raise Unavailable("normalized_TWD_shares_and_consolidated_scope_required")
    refs = packet.get("document_ids", [])
    if not refs or len(refs) != len(set(refs)):
        raise Unavailable("distinct_document_references_required")
    for key in refs:
        doc = documents[key]
        if doc.get("stock_id") != stock:
            raise ValueError("Source company mismatch")
        if not doc.get("revision_id") or not doc.get("source_url", "").startswith("https://"):
            raise ValueError("Source URL and numeric revision required")
        content = base64.b64decode(doc["content_base64"], validate=True)
        if hashlib.sha256(content).hexdigest() != doc["sha256"]:
            raise ValueError("Archived review document hash mismatch")
        if not doc.get("captured_at"):
            raise Unavailable("capture_time_missing")
        published = doc.get("published_at")
        if not published:
            if (
                doc.get("publication_time_status") != "unknown_current_revision"
                or doc.get("public_capture_verified") is not True
            ):
                raise Unavailable("publication_or_public_capture_proof_missing")
            # Public capture establishes current knowledge, never an initial
            # announcement date or availability before this numeric capture.
            published = doc["captured_at"]
        # A date-only announcement cannot establish availability earlier that day.
        published = _end_of_day(published) if len(published) == 10 else timestamp(published)
        if len(doc["captured_at"]) == 10:
            raise Unavailable("capture_timestamp_precision_required")
        if max(published, timestamp(doc["captured_at"])) > cutoff:
            raise Unavailable("source_not_available_at_cutoff")
    values = packet.get("values", {})
    if any(key not in values for key in fields):
        raise Unavailable("required_financial_values_missing")
    if (
        packet.get("period_end")
        and date.fromisoformat(packet["period_end"]) > cutoff.astimezone(TAIPEI).date()
    ):
        raise Unavailable("future_financial_period")
    return {key: finite(values[key]) for key in fields}


def _compatible(current, previous):
    for key in ("stock_id", "scope", "unit", "version_set"):
        if current.get(key) != previous.get(key):
            raise Unavailable("unavailable_version_or_scope_mismatch")


def _earnings(packet, stock, documents, cutoff):
    fields = (
        "revenue_twd",
        "operating_profit_twd",
        "parent_profit_twd",
        "diluted_numerator_twd",
        "numerator_adjustment_twd",
        "basic_weighted_shares",
        "diluted_weighted_shares",
    )
    v = _packet(packet, stock, documents, cutoff, fields)
    _period(packet, "single_quarter_diluted")
    if min(v["revenue_twd"], v["basic_weighted_shares"], v["diluted_weighted_shares"]) <= 0:
        raise ValueError("Positive revenue and weighted shares required")
    if packet.get("diluted_note_verified") is not True or "dilution_components" not in packet:
        raise Unavailable("diluted_numerator_and_denominator_note_required")
    components, seen = packet["dilution_components"], set()
    for component in components:
        key = component["event_id"]
        if not key or key in seen or finite(component["weighted_shares"]) < 0:
            raise ValueError("Duplicate or invalid diluted share component")
        seen.add(key)
    if set(packet.get("incremental_event_ids", [])) & seen:
        raise ValueError("Dilution already included in reported denominator")
    denominator = v["basic_weighted_shares"] + sum(finite(c["weighted_shares"]) for c in components)
    if abs(denominator - v["diluted_weighted_shares"]) > 0.01:
        raise ValueError("Diluted denominator does not reconcile")
    if (
        abs(v["parent_profit_twd"] + v["numerator_adjustment_twd"] - v["diluted_numerator_twd"])
        > 0.01
    ):
        raise ValueError("Diluted numerator does not reconcile")
    return {
        **v,
        "eps_twd": v["diluted_numerator_twd"] / v["diluted_weighted_shares"],
        "operating_margin": v["operating_profit_twd"] / v["revenue_twd"],
        "parent_margin": v["parent_profit_twd"] / v["revenue_twd"],
        "period_start": packet["period_start"],
        "period_end": packet["period_end"],
        "document_ids": packet["document_ids"],
        "locator": packet["locator"],
    }


def earnings_review(current, previous, stock, documents, cutoff):
    now, prior = (_earnings(p, stock, documents, cutoff) for p in (current, previous))
    _compatible(current, previous)
    if (
        int(current["period_end"][:4]) - int(previous["period_end"][:4]) != 1
        or current["period_end"][4:] != previous["period_end"][4:]
    ):
        raise Unavailable("same_quarter_prior_year_required")
    parent_up = now["parent_profit_twd"] > prior["parent_profit_twd"]
    eps_up = now["eps_twd"] > prior["eps_twd"]
    return {
        "status": "both_increased"
        if parent_up and eps_up
        else "both_not_increased"
        if not parent_up and not eps_up
        else "mixed",
        "current": now,
        "prior": prior,
        "eps_change_twd": now["eps_twd"] - prior["eps_twd"],
        "parent_profit_change_twd": now["parent_profit_twd"] - prior["parent_profit_twd"],
        "operating_margin_change_pp": 100 * (now["operating_margin"] - prior["operating_margin"]),
        "parent_margin_change_pp": 100 * (now["parent_margin"] - prior["parent_margin"]),
        "interpretation": "Realized accounting earnings, not cash returned or stock investment return",
    }


def cash_quarter(current, previous, stock, documents, cutoff):
    """Amounts only. Version sets attest reviewed equivalence, never inferred by date."""
    fields = ("cfo_twd", "revenue_twd")
    now = _packet(current, stock, documents, cutoff, fields)
    _, end = _period(current, "ytd")
    if end.month == 3:
        prior = dict.fromkeys(fields, 0)
    else:
        prior = _packet(previous, stock, documents, cutoff, fields)
        _period(previous, "ytd")
        _compatible(current, previous)
        expected_month = end.month - 3
        expected = date(end.year, expected_month, calendar.monthrange(end.year, expected_month)[1])
        if previous["period_end"] != expected.isoformat():
            raise Unavailable("immediately_preceding_same_year_YTD_required")
        if current.get("bridge_verified") is not True or not current.get("bridge_locator"):
            raise Unavailable("YTD_version_bridge_review_required")
    quarter = {key: now[key] - prior[key] for key in fields}
    if quarter["revenue_twd"] <= 0:
        raise ValueError("Positive same-quarter revenue required")
    return {
        "status": "nonnegative_cfo" if quarter["cfo_twd"] >= 0 else "negative_cfo",
        "period_start": date(end.year, end.month - 2, 1).isoformat(),
        "period_end": end.isoformat(),
        "single_quarter": quarter,
        "cfo_revenue_ratio": quarter["cfo_twd"] / quarter["revenue_twd"],
        "ytd": now,
        "collection_confirmed": False,
        "document_ids": list(
            dict.fromkeys(
                current["document_ids"]
                + (previous["document_ids"] if previous and end.month != 3 else [])
            )
        ),
        "version_set": current["version_set"],
        "interpretation": "Program-differenced amounts; positive CFO alone does not prove receivables collection or sustainability",
    }


def capital_review(event, earnings, stock, documents, cutoff):
    _packet(event, stock, documents, cutoff)
    status = event.get("event_status")
    if status == "authorized":
        return {
            "status": "authorization_only",
            "recompute_eps": False,
            "reason": "Authorized maximum is not an issue or a weighted denominator",
        }
    if status != "issued":
        raise Unavailable("completed_issue_not_verified")
    if (
        not event.get("event_id")
        or not event.get("issue_date")
        or not event.get("completion_locator")
    ):
        raise Unavailable("issue_date_amount_and_completion_evidence_required")
    if date.fromisoformat(event["issue_date"]) > cutoff.astimezone(TAIPEI).date():
        raise Unavailable("issue_not_effective_at_cutoff")
    if event.get("actual_new_shares") is None:
        raise Unavailable("actual_issue_amount_missing")
    shares = finite(event["actual_new_shares"])
    if shares <= 0 or not shares.is_integer():
        raise ValueError("Positive actual issued shares required")
    _earnings(earnings, stock, documents, cutoff)
    _compatible(event, earnings)
    if event["issue_date"] > earnings["period_end"]:
        raise Unavailable("await_following_quarter_EPS_note")
    if event["issue_date"] < earnings["period_start"]:
        raise Unavailable("prior_issue_requires_opening_share_register_bridge")
    if not event.get("eps_note_reconciled") or not event.get("eps_note_locator"):
        raise Unavailable("issue_to_weighted_EPS_note_bridge_required")
    if event["event_id"] not in earnings.get("basic_share_event_ids", []):
        raise Unavailable("issue_not_linked_to_basic_weighted_share_note")
    if event["event_id"] in {c["event_id"] for c in earnings["dilution_components"]}:
        raise ValueError("Ordinary issue also counted as potential dilution")
    register = event.get("share_register_bridge")
    if not register or not register.get("locator"):
        raise Unavailable("issued_share_register_bridge_required")
    opening, closing, other = (
        finite(register[k]) for k in ("opening_shares", "closing_shares", "other_net_shares")
    )
    if min(opening, closing) <= 0 or abs(opening + shares + other - closing) > 0.01:
        raise ValueError("Actual issue amount does not reconcile to share register")
    return {
        "status": "issued_and_note_reconciled",
        "event_id": event["event_id"],
        "actual_new_shares": shares,
        "issue_date": event["issue_date"],
        "recompute_eps": False,
        "reason": "Reviewed actual event is already reflected in reported weighted shares; do not add twice",
    }


def _attempt(function, *args):
    try:
        return function(*args)
    except Unavailable as exc:
        return {"status": "unavailable", "reason": str(exc)}


def review(stock, as_of, inputs=None, model=None, short_term=None, clock=None, snapshot=None):
    """A report always carries four independent axes, including explicit missingness."""
    data = inputs or {}
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None:
        raise ValueError("Review clock timezone required")
    cutoff = timestamp(data["cutoff_at"]) if data.get("cutoff_at") else min(_end_of_day(as_of), now)
    if cutoff > min(_end_of_day(as_of), now):
        raise ValueError("Review cutoff exceeds report date or actual creation time")
    if cutoff.astimezone(TAIPEI).date().isoformat() != as_of:
        raise ValueError("Review cutoff must be on the report analysis date")
    if data and data.get("stock_id") != stock:
        raise ValueError("Review input company mismatch")
    docs = data.get("documents", {})
    earnings = _attempt(
        earnings_review, data.get("earnings"), data.get("prior_earnings"), stock, docs, cutoff
    )
    cash = _attempt(
        cash_quarter, data.get("cash_ytd"), data.get("previous_cash_ytd"), stock, docs, cutoff
    )

    def ytd_observation():
        values = _packet(data.get("cash_ytd"), stock, docs, cutoff, ("cfo_twd", "revenue_twd"))
        _period(data["cash_ytd"], "ytd")
        return {
            "status": "available",
            "period_start": data["cash_ytd"]["period_start"],
            "period_end": data["cash_ytd"]["period_end"],
            "values": values,
            "basis": "ytd_not_single_quarter",
        }

    cash["ytd_observation"] = _attempt(ytd_observation)
    if cash["ytd_observation"]["status"] == "available":
        cash.setdefault("ytd", cash["ytd_observation"]["values"])
    if "ytd" in cash and data.get("prior_year_cash_ytd"):

        def compare_ytd():
            prior = data["prior_year_cash_ytd"]
            values = _packet(prior, stock, docs, cutoff, ("cfo_twd", "revenue_twd"))
            _period(prior, "ytd")
            _compatible(data["cash_ytd"], prior)
            current_end, prior_end = data["cash_ytd"]["period_end"], prior["period_end"]
            if int(current_end[:4]) - int(prior_end[:4]) != 1 or current_end[4:] != prior_end[4:]:
                raise Unavailable("same_YTD_months_prior_year_required")
            if values["revenue_twd"] <= 0 or cash["ytd"]["revenue_twd"] <= 0:
                raise ValueError("Positive YTD revenue required")
            current_ratio = cash["ytd"]["cfo_twd"] / cash["ytd"]["revenue_twd"]
            prior_ratio = values["cfo_twd"] / values["revenue_twd"]
            return {
                "status": "available",
                "cfo_change_twd": cash["ytd"]["cfo_twd"] - values["cfo_twd"],
                "current_cfo_revenue_ratio": current_ratio,
                "prior_cfo_revenue_ratio": prior_ratio,
                "ratio_change_pp": 100 * (current_ratio - prior_ratio),
                "interpretation": "Absolute funding need and scale-normalized cash conversion can move in different directions",
            }

        cash["same_YTD_comparison"] = _attempt(compare_ytd)
    event_ids = [e.get("event_id") for e in data.get("capital_events", [])]
    if any(not e for e in event_ids) or len(event_ids) != len(set(event_ids)):
        raise ValueError("Distinct capital event identifiers required")
    capital = [
        _attempt(capital_review, e, data.get("earnings"), stock, docs, cutoff)
        for e in data.get("capital_events", [])
    ]
    # A balance decrease or CFO ratio cannot establish actual receipts.
    cash["collection_evidence"] = {
        "status": "unavailable",
        "reason": "Separate archived cash-receipt/noncash reconciliation required; not inferred from balances",
    }
    valuation = {
        "status": "conditional_prices_only"
        if model and model.get("status") == "ready"
        else "unavailable",
        "undervaluation_established": False,
        "fair_value": None,
        "reason": "Scenario assumptions do not independently establish cheapness or fair value",
    }
    trade = (short_review(snapshot, cutoff) if snapshot is not None else short_term) or {
        "status": "unavailable",
        "reason": "Complete adjusted bars and calendar required",
    }
    result = {
        "schema_version": 1,
        "stock_id": stock,
        "cutoff_at": cutoff.isoformat(),
        "axes": dict(zip(AXES, [earnings, cash, valuation, trade], strict=True)),
        "capital_events": capital,
        "input_sha256": fingerprint(data),
        "conditions": [
            {
                "axis": "accounting_earnings",
                "rule": "同公司、同季同比：歸母與精算稀釋 EPS 皆增加／皆未增加／分歧；附註不足不可用。",
            },
            {
                "axis": "cash_sustainability",
                "rule": "僅相鄰同年、同範圍、同單位且版本銜接已核對的累計金額可差分；另列同比絕對額及營收比，不能差分累計 EPS。",
            },
            {"axis": "valuation", "rule": "條件價格與合理價分開；缺獨立估值驗證，不成立低估判斷。"},
            {
                "axis": "short_term",
                "rule": "固定量價與暫定風報比檢查；下一實際開盤須重算，不用長期估值代替短線確認。",
            },
        ],
        "trust_boundary": "Named reviewer attests company mapping, numeric transcription and version equivalence; bytes/hash/date checks cannot prove their correctness",
        "orders": False,
        "llm_api_calls": False,
    }
    result["review_sha256"] = fingerprint(result)
    return result


def short_review(snapshot, cutoff=None):
    """Use the existing fixed short-horizon rule, independent of four-quarter PE."""
    from tradingagents.dataflows.taiwan.calendar import verify_calendar
    from tradingagents.dataflows.taiwan.extensions import ADJUSTED

    from .trade_rules import setup

    calendar_data = snapshot.get("market_calendar", {})
    if calendar_data.get("status") != "ok":
        return {"status": "unavailable", "reason": "Complete official calendar required"}
    sessions = [
        d
        for d in verify_calendar(calendar_data, snapshot["instrument"]["exchange"])
        if d < snapshot["as_of"]
    ][-22:]
    rows = snapshot.get("extensions", {}).get(ADJUSTED, {}).get("records", [])
    by_date = {r["data_date"]: r for r in rows}
    if len(by_date) != len(rows):
        raise ValueError("Duplicate adjusted price session")
    if len(sessions) < 22 or any(d not in by_date for d in sessions):
        return {
            "status": "unavailable",
            "reason": "22 complete stock sessions required; missing bars not filled",
        }
    if cutoff is not None and any(
        not by_date[d].get("retrieved_at") or timestamp(by_date[d]["retrieved_at"]) > cutoff
        for d in sessions
    ):
        return {
            "status": "unavailable",
            "reason": "Adjusted price capture unavailable at review cutoff",
        }
    bars = [{"date": d, "basis": "vendor_adjusted", **by_date[d]["values"]} for d in sessions]
    return setup(bars, sessions)
