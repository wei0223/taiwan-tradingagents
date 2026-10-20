"""Period-matched cash diagnostics; balance movements are never called collections."""

from datetime import date

from .review import Unavailable, _compatible, _packet, _period
from .scenarios import finite


def analyze(stock, documents, cutoff, packets, reviewed):
    earnings = reviewed["axes"]["accounting_earnings"].get("current", {})
    cash = reviewed["axes"]["cash_sustainability"].get("single_quarter", {})
    parent = earnings.get("parent_profit_twd")
    result = {
        "cfo_to_parent_profit_descriptive": cash["cfo_twd"] / parent
        if cash.get("cfo_twd") is not None and parent and parent > 0
        else None,
        "ratio_caveat": "Consolidated CFO divided by parent profit has an ownership-scope mismatch; not a cross-industry quality score",
        "collection_evidence": "unavailable_without_cash_non_cash_impairment_reclassification_bridge",
        "negative_cfo_verdict": "requires_explanation_not_automatic_bearish",
        "details": {
            "status": "unavailable",
            "missing": ["reviewed_cash_detail_and_opening_balances"],
        },
    }
    if not packets:
        return result
    fields = (
        "revenue_twd",
        "cogs_twd",
        "net_profit_twd",
        "cfo_twd",
        "depreciation_twd",
        "capex_ppe_twd",
        "capex_intangibles_twd",
        "ar_cash_effect_twd",
        "inventory_cash_effect_twd",
        "ap_cash_effect_twd",
        "financing_cashflow_twd",
    )
    try:
        flow = packets["flow"]
        v = _packet(flow, stock, documents, cutoff, fields)
        start, end = _period(flow, "ytd")
        if any(
            v[k] < 0
            for k in (
                "revenue_twd",
                "cogs_twd",
                "depreciation_twd",
                "capex_ppe_twd",
                "capex_intangibles_twd",
            )
        ):
            raise ValueError("Cash capex is a positive cash-payment amount, not a signed flow")
        details = {
            "status": "available",
            "basis": "ytd",
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "source_packet": flow,
            "values": v,
            "cfo_to_consolidated_profit": v["cfo_twd"] / v["net_profit_twd"]
            if v["net_profit_twd"] > 0
            else None,
            "fcf_ppe_only_twd": v["cfo_twd"] - v["capex_ppe_twd"],
            "fcf_ppe_and_intangibles_twd": v["cfo_twd"]
            - v["capex_ppe_twd"]
            - v["capex_intangibles_twd"],
            "fcf_definition": "Cash payments for PPE and optionally intangibles; asset prepayments, leases and acquisitions are not silently included",
            "ar_inventory_ap_cash_effect_twd": sum(
                v[k]
                for k in ("ar_cash_effect_twd", "inventory_cash_effect_twd", "ap_cash_effect_twd")
            ),
            "other_cash_bridge_residual_twd": v["cfo_twd"]
            - sum(
                v[k]
                for k in ("ar_cash_effect_twd", "inventory_cash_effect_twd", "ap_cash_effect_twd")
            ),
            "cash_bridge_complete": False,
            "days": {
                "status": "unavailable",
                "reason": "Matched opening and ending balances required",
            },
        }
        if packets.get("opening") and packets.get("ending"):
            opening, ending = packets["opening"], packets["ending"]
            for balance in (opening, ending):
                if (
                    balance.get("basis") != "balance"
                    or balance.get("period_start")
                    or balance.get("period_end")
                ):
                    raise ValueError("Point-in-time balance packet must not inherit flow periods")
            _compatible(flow, opening)
            _compatible(flow, ending)
            expected_open = date(start.year - 1, 12, 31).isoformat()
            if (
                opening.get("as_of_date") != expected_open
                or ending.get("as_of_date") != end.isoformat()
            ):
                raise ValueError("Balances must bracket the flow period")
            balance_fields = ("receivables_twd", "inventory_twd", "payables_twd")
            a = _packet(opening, stock, documents, cutoff, balance_fields)
            b = _packet(ending, stock, documents, cutoff, balance_fields)
            if any(x < 0 for x in [*a.values(), *b.values()]):
                raise ValueError("Negative operating balance")
            days = (end - start).days + 1
            if min(v["revenue_twd"], v["cogs_twd"]) <= 0:
                raise Unavailable("Positive revenue and COGS required for days")
            dso = (a["receivables_twd"] + b["receivables_twd"]) / 2 / v["revenue_twd"] * days
            dio = (a["inventory_twd"] + b["inventory_twd"]) / 2 / v["cogs_twd"] * days
            dpo = (a["payables_twd"] + b["payables_twd"]) / 2 / v["cogs_twd"] * days
            details["days"] = {
                "status": "available_proxy",
                "days_in_period": days,
                "dso": dso,
                "dio": dio,
                "dpo_cogs_proxy": dpo,
                "ccc_cogs_proxy": dso + dio - dpo,
                "opening": opening,
                "ending": ending,
                "limitation": "Two-point average and COGS instead of credit purchases; not contractual collection/payment terms",
            }
        result["details"] = details
    except (Unavailable, KeyError) as exc:
        result["details"] = {"status": "unavailable", "missing": [str(exc)]}
    return result


def normalize_flow(net_shares, volume_shares, free_float_shares=None):
    net, volume = finite(net_shares), finite(volume_shares)
    if volume < 0:
        raise ValueError("Negative window volume")
    if free_float_shares is not None and finite(free_float_shares) <= 0:
        raise ValueError("Positive verified free float required")
    return {
        "net_shares": net,
        "window_volume_shares": volume,
        "net_to_volume_pct": net / volume * 100 if volume else None,
        "net_to_free_float_pct": net / free_float_shares * 100 if free_float_shares else None,
        "free_float_status": "available"
        if free_float_shares
        else "unavailable_not_weighted_diluted_shares",
    }
