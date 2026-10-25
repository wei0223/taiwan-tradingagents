"""Adapt the existing original-review packets into daily per-field observations."""

import copy

from tradingagents.dataflows.taiwan.periods import refresh_snapshot

from .review import Unavailable, _earnings, _end_of_day, _packet, _period
from .scenarios import timestamp

FIELDS = {"revenue_twd": "Revenue", "gross_profit_twd": "GrossProfit",
          "cost_of_revenue_twd": "CostOfGoodsSold", "operating_expenses_twd": "OperatingExpenses",
          "operating_profit_twd": "OperatingIncome", "consolidated_net_twd": "IncomeAfterTaxes",
          "parent_profit_twd": "EquityAttributableToOwnersOfParent", "nci_twd": "NoncontrollingInterests",
          "pretax_twd": "PretaxProfit", "tax_twd": "TaxExpense", "cfo_twd": "CashFlowsFromOperatingActivities",
          "ppe_cash_twd": "PropertyAndPlantAndEquipment", "basic_weighted_shares": "BasicWeightedShares",
          "capex_ppe_twd": "PropertyAndPlantAndEquipment",
          "diluted_weighted_shares": "DilutedWeightedShares"}


def prepare(snapshot, review_input=None, extension_input=None):
    stock, as_of = snapshot["instrument"]["stock_id"], snapshot["as_of"]
    data = review_input or {}
    cutoff = timestamp(data["cutoff_at"]) if data.get("cutoff_at") else _end_of_day(as_of)
    docs = data.get("documents", {})
    ledger = {"stock_id": stock, "documents": {}, "observations": [], "bridges": []}
    rejected = []

    def add(name, packet):
        if not packet or packet.get("basis") not in {"single_quarter", "single_quarter_diluted", "ytd"}:
            return
        try:
            _period(packet, packet["basis"])
            values = _packet(packet, stock, docs, cutoff, tuple(packet.get("values", {})))
        except Unavailable as exc:
            rejected.append({"packet": name, "reason": str(exc)})
            return
        for key in packet["document_ids"]:
            ledger["documents"][key] = copy.deepcopy(docs[key])
        for original, field in FIELDS.items():
            if original not in values:
                continue
            unit = "shares" if field.endswith("Shares") else "TWD"
            value = values[original]
            if original == "capex_ppe_twd":
                if value < 0:
                    raise ValueError("Reviewed positive cash capex outflow convention required")
                value = -value
            ledger["observations"].append({
                "id": name + "." + field, "stock_id": stock, "field": field, "value": value,
                "original_field": original, "original_reviewed_value": values[original],
                "normalization": "positive_cash_capex_outflow_to_negative_flow" if original == "capex_ppe_twd" else "identity",
                "period_start": packet["period_start"], "period_end": packet["period_end"],
                "basis": "ytd" if packet["basis"] == "ytd" else "single_quarter",
                "scope": packet["scope"], "unit": unit, "version_set": packet["version_set"],
                "statement_type": "cash_flow" if original in {"cfo_twd", "ppe_cash_twd", "capex_ppe_twd"}
                                  else "eps_note" if unit == "shares" else "income",
                "document_ids": packet["document_ids"], "locator": packet["locator"],
                "reviewed_by": packet["reviewed_by"], "values_reviewed": True})
        if packet["basis"] == "single_quarter_diluted":
            try:
                checked = _earnings(packet, stock, docs, cutoff)
            except Unavailable as exc:
                rejected.append({"packet": name, "reason": str(exc)})
                return
            # Weighted-share EPS calculation remains separate; never YTD EPS subtraction.
            obs = next(r for r in ledger["observations"] if r["id"] == name + ".Revenue")
            value = packet.get("reported_diluted_eps_twd", checked["eps_twd"])
            if abs(value - checked["eps_twd"]) > 0.005:
                raise ValueError("Reported diluted EPS differs from reviewed note calculation")
            ledger["observations"].append({**obs, "id": name + ".DilutedEPS", "field": "DilutedEPS",
                                           "value": value, "unit": "TWD/share", "eps_kind": "diluted"})

    for name, packet in (extension_input or {}).get("financial_packets", {}).items():
        kind = data.get("financial_period_packet_selection", {}).get(name, packet.get("statement_kind"))
        if kind not in {"income_statement", "cash_flow_statement", "eps_note"}:
            rejected.append({"packet": "extension." + name,
                             "reason": "accounting_statement_type_not_explicitly_reviewed; monthly sums and balances are separate"})
            continue
        add("extension." + name, packet)
    for name in ("earnings", "prior_earnings", "cash_ytd", "previous_cash_ytd", "prior_year_cash_ytd"):
        add("review." + name, data.get(name))
    current, previous = data.get("cash_ytd", {}), data.get("previous_cash_ytd", {})
    bridge_review = data.get("version_bridge_review", {})
    refs = set(current.get("document_ids", []) + previous.get("document_ids", []))
    if (current.get("bridge_verified") is True
            and bridge_review.get("status") == "current_version_amount_bridge_reviewed"
            and refs and all(bridge_review.get("documents", {}).get(k) == docs[k]["sha256"] for k in refs)):
        ids = {r["id"] for r in ledger["observations"]}
        for field in ("CashFlowsFromOperatingActivities", "Revenue", "PropertyAndPlantAndEquipment"):
            a, b = "review.cash_ytd." + field, "review.previous_cash_ytd." + field
            if a in ids and b in ids:
                ledger["bridges"].append({"current_id": a, "previous_id": b, "field": field,
                                          "version_set": current["version_set"], "bridge_verified": True,
                                          "document_sha256s": sorted({docs[k]["sha256"] for k in refs}),
                                          "locator": current["locator"] + " / " + previous["locator"],
                                          "reviewed_by": current["reviewed_by"]})
    for obs in data.get("financial_period_observations", []):
        ledger["observations"].append(copy.deepcopy(obs))
        for key in obs.get("document_ids", []):
            ledger["documents"][key] = copy.deepcopy(docs[key])
    ledger["bridges"].extend(copy.deepcopy(data.get("financial_period_bridges", [])))
    # An explicitly supplied standalone period review can be consumed without the adapter.
    if not ledger["observations"]:
        ledger = snapshot.get("financial_period_review")
    corrected = refresh_snapshot(snapshot, ledger)
    corrected["financial_period_adapter_audit"] = {"rejected_packets": rejected,
                                                  "raw_values_modified": False,
                                                  "historical_vintage_upgraded": False}
    return corrected


def audit(original, corrected):
    from .bundle import fingerprint

    def raw_values(snapshot):
        return [{"dataset": k, "records": [{"raw": r["raw"], "values": r["values"]}
                                             for r in v.get("records", [])], "raw_rows": v.get("raw_rows", [])}
                for group in (snapshot.get("datasets", {}), snapshot.get("extensions", {}))
                for k, v in sorted(group.items())]

    fields = ("eps_twd", "eps_qoq_pct", "eps_yoy_pct", "gross_margin_pct", "operating_margin_pct", "net_margin_pct")
    before = {r["period"]: r for r in original.get("quarterly_financials", [])}
    changes = [{"period": row["period"], "field": key,
                "old_cached_unverified": before.get(row["period"], {}).get(key), "corrected": row.get(key)}
               for row in corrected.get("quarterly_financials", []) for key in fields
               if before.get(row["period"], {}).get(key) != row.get(key)]
    return {"policy": "reviewed_single_quarter_only_v1", "metric_changes": changes,
            "raw_values_unchanged": fingerprint(raw_values(original)) == fingerprint(raw_values(corrected)),
            "forecast_modified": False, "historical_initial_vintage_proven": False,
            "rejected_packets": corrected.get("financial_period_adapter_audit", {}).get("rejected_packets", [])}


def render_section(snapshot, period_audit, table):
    section = '<section id="financial-period-conversion"><h2>財報期間修正與影響</h2><p>單季指標只使用已核對起訖日、範圍、單位及版本的原件。原始供應商數值保持不變；累計 EPS、加權股數與期末餘額不作差分。<a href="period-conversion.json">逐項修正</a> · <a href="source-snapshot.json">修正前來源包</a>。</p>'
    changes = period_audit["metric_changes"][-24:]
    section += table(["期間", "指標", "舊快取（未核對）", "修正後"],
                     [[r["period"], r["field"], r["old_cached_unverified"],
                       r["corrected"] if r["corrected"] is not None else "不可用"] for r in changes]) if changes else '<p>本次沒有可比較的舊快取變更；此項不代表所有原件已齊。</p>'
    cash = [r for r in snapshot.get("extended_research", {}).get("cashflow", [])
            if r.get("operating_twd_as_reported") is not None][-2:]
    if cash:
        def show(value):
            return f"{value:.3f}" if value is not None else "不可用"
        section += '<h3>同版本單季現金轉換</h3>' + table(
            ["期間", "CFO（億元）", "CFO／營收", "CFO／歸母淨利", "單季 FCF（億元）"],
            [[r["period"], show(r["operating_twd_as_reported"] / 1e8), show(r["cfo_to_revenue"]),
              show(r["cfo_to_parent_profit"]), show(r["operating_less_ppe_twd_as_reported"] / 1e8)
              if r["operating_less_ppe_twd_as_reported"] is not None else "不可用"] for r in cash])
        section += '<p>這是同期間觀測，不作跨產業排名。單季現金資本支出未核對時，FCF 不可用；後文累計來源表只能按其原期間閱讀。</p>'
    return section + '</section>'
