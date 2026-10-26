"""Independent evidence, calibration, revision and valuation disclosures."""

from .bundle import fingerprint
from .event_status import inventory
from .financial_model import waterfall
from .forecast_journal import evaluate
from .model_revision import compare
from .scenarios import timestamp


def revenue_diagnostic(history, stock, cutoff):
    from tradingagents.dataflows.taiwan.calculations import revenue_metrics

    from .bundle import build_bundle
    from .revenue_projection import project

    if history["instrument"]["stock_id"] != stock or history["as_of"] > cutoff[:10]:
        raise ValueError("Historical revenue company/date mismatch")
    build_bundle(history)  # Revalidate raw rows and publication-vintage bindings.
    records = history["datasets"]["TaiwanStockMonthRevenue"]["records"]
    if any(timestamp(r["retrieved_at"]) > timestamp(cutoff) for r in records):
        raise ValueError("Revenue diagnostic captured after cutoff")
    result = project({"as_of": history["as_of"], "monthly_revenue": revenue_metrics(records)})
    result["source_snapshot_sha256"] = fingerprint(history)
    result["available_at"] = max((r["retrieved_at"] for r in records), default=None)
    return result


def parameter_dimensions(forward, register, documents, cutoff):
    result = []
    cases = {(s, c["period"]): c for s, m in forward.get("scenarios", {}).items() for c in m["quarters"]}
    for row in register:
        case = cases[(row["scenario"], row["period"])]
        values = {k: v["value"] for k, v in case["inputs"].items()}
        for param in row["parameters"]:
            key, value = param["parameter_name"], param["value"]
            sources = [{"source_id": sid, "sha256": documents[sid].get("sha256"),
                        "published_at": documents[sid].get("published_at"),
                        "captured_at": documents[sid].get("captured_at"),
                        "known_since": max((documents[sid].get("published_at") or documents[sid]["captured_at"],
                                            documents[sid]["captured_at"]), key=timestamp)}
                       for sid in param["source"] if sid in documents]
            alternatives = sorted({c["inputs"][key]["value"] for (s, q), c in cases.items()
                                   if q == row["period"] and c["inputs"].get(key, {}).get("value") is not None
                                   and c["inputs"][key]["value"] != value})
            if not alternatives and value is not None:
                if key.endswith(("ratio", "fraction")) or key == "tax_rate":
                    alternatives = [value - .01 if value > .99 else value + .01]
                elif key == "diluted_weighted_shares":
                    alternatives = [value * 1.01]
                else:
                    alternatives = [value + max(abs(value) * .1, abs(values.get("revenue_twd") or 0) * .001, 1)]
                kind = "mechanical_one_factor_stress_not_independent_forecast"
            else:
                kind = "other_scenario_assumption_not_probability"
            sensitivities = []
            if all(v is not None for v in values.values()):
                base = waterfall(values)
                for alternative in alternatives:
                    stress = waterfall({**values, key: alternative})
                    sensitivities.append({"alternative": alternative, "eps_twd": stress["eps_twd"],
                                          "eps_delta_twd": stress["eps_twd"] - base["eps_twd"],
                                          "parent_profit_delta_twd": stress["parent_profit_twd"] - base["parent_profit_twd"]})
            complete = param["validity_status"] == "reviewed"
            # A source-backed historical anchor is not evidence that an extrapolation
            # is correct. No caller flag promotes these assumptions to calibrated.
            result.append({**param, "scenario": row["scenario"], "period": row["period"],
                           "dimensions": {"completeness": "PASS" if complete else "FAIL",
                                          "future_predictive_evidence": "FAIL", "forecast_calibration": "FAIL"},
                           "evidence_grade": "historical_anchor_plus_explicit_assumption"
                           if complete else "missing_or_unverified",
                           "formula": param.get("rationale"), "source_availability": sources,
                           "alternatives_kind": kind, "alternatives": alternatives,
                           "sensitivity": sensitivities, "historical_error": {"n": 0, "mae": None, "mape": None},
                           "uncertainty_is_probability": False, "evaluated_at": cutoff})
    return result


def historical_pe(price_rows, eps_rows, stock, cutoff):
    """Join explicit quarter disclosures to each price's information boundary.

    Current captures are retained as diagnostics; no vendor PE denominator is
    inferred. Split/corporate-action basis requires a source-bound review too.
    """
    from tradingagents.dataflows.taiwan.periods import _document, cutoff_time

    from .vintage import knowledge

    def original_verified(row, boundary):
        try:
            doc = row["document"]
            _document(doc, stock, cutoff_time(cutoff))
            availability = {**doc, **{k: row[k] for k in ("historical_version_proof", "object_id", "revision_id") if k in row}}
            return bool(row.get("reviewed_by") and row.get("locator")
                        and knowledge(availability, boundary, cutoff)["eligible"]
                        and row["source_sha256"] == doc["sha256"]
                        and row["source_url"] == doc["source_url"])
        except (KeyError, ValueError, TypeError):
            return False

    rows = []
    for price in price_rows:
        boundary = price["date"] + "T16:30:00+08:00"
        if timestamp(boundary) > timestamp(cutoff):
            continue
        eligible = [e for e in eps_rows if e.get("stock_id") == stock
                    and e.get("basis") == "direct_reported_single_quarter_diluted_eps"
                    and e.get("source_sha256") and e.get("source_url") and e.get("locator")
                    and e.get("original_bytes_verified") is True
                    and original_verified(e, boundary)
                    and e.get("published_at") and timestamp(e["published_at"]) <= timestamp(boundary)]
        by_period = {}
        for e in eligible:
            by_period.setdefault(e["period"], []).append(e)
        periods = sorted(by_period)[-4:]
        selected = [by_period[p][0] for p in periods if len(by_period[p]) == 1]
        indices = [int(e["period"][:4]) * 4 + int(e["period"][-1]) for e in selected]
        reasons = []
        if len(indices) != 4 or indices != list(range(indices[0], indices[0] + 4)):
            reasons.append("four_consecutive_unambiguous_known_quarter_originals_missing")
        if not periods or price.get("expected_latest_reported_period") != periods[-1]:
            reasons.append("latest_announced_earnings_period_not_verified")
        action = price.get("corporate_action_review", {})
        if not (price.get("source_sha256") and price.get("stock_id") == stock
                and price.get("price_basis") == "unadjusted_TWD_per_common_share"
                and original_verified(action, boundary)
                and price.get("raw") and price["source_sha256"] == fingerprint(price["raw"])
                and price["raw"].get("close") == price["close"]
                and price["raw"].get("date") == price["date"]
                and knowledge({**price, "public_capture_verified": True,
                               "publication_time_status": "unknown_current_revision"}, boundary, cutoff)["eligible"]):
            reasons.append("contemporaneous_price_or_common_share_basis_unverified")
        eps = sum(e["eps_twd"] for e in selected) if not reasons else None
        rows.append({"date": price["date"], "price_twd": price["close"],
                     "price_source_sha256": price.get("source_sha256"), "information_boundary": boundary,
                     "quarters": selected, "eps_period_start": periods[0] if periods else None,
                     "eps_period_end": periods[-1] if periods else None, "ttm_eps": eps,
                     "pe": price["close"] / eps if eps and eps > 0 else None,
                     "status": "VERIFIED" if not reasons else "SOURCE_UNAVAILABLE", "reason": reasons[-1] if reasons else None,
                     "exclusion_reasons": reasons})
    return {"rows": rows, "verified_points": sum(r["status"] == "VERIFIED" for r in rows),
            "fair_exit_multiple_established": False, "undervaluation_rank": None,
            "basis": "reported_quarter_diluted_eps_sum_not_statutory_annual_weighted_eps"}


def build(snapshot, result, extension, documents):
    plan = result["extension"]["research_plan"]
    cutoff = (plan.get("context") or {}).get("cutoff_at")
    if not cutoff:
        return {"status": "unavailable_no_research_plan", "summary": ["V5：缺研究計畫與精確截止時間。"]}
    spec = extension.get("research_plan", {})
    forward = plan.get("forward", {})
    parameters = parameter_dimensions(forward, result["publishing_quality"]["forward_parameter_register"], documents, cutoff)
    events = inventory(plan, documents, spec)
    registry = result.get("official_source_registry", {})
    for family, payload in sorted(registry.get("families", {}).items()):
        for key in sorted(payload.get("pending_keys", [])):
            events["events"].append({"source_id": "registry:" + key, "published_at": None,
                "categories": [family], "status": "SOURCE_UNAVAILABLE", "material": True,
                "reason": "required_family_item_publication_or_selected_original_not_verified",
                "model_impact": "expected_item_is_not_proof_it_has_been_announced", "hard_block": True})
    if registry.get("historical_event_links_pending"):
        events["events"].append({"source_id": "registry:historical_version_bindings", "published_at": None,
            "categories": ["historical_versions"], "status": "REVISION_UNRESOLVED", "material": True,
            "reason": "historical_event_to_filename_version_binding_pending",
            "model_impact": "strict_historical_backtest_blocked_not_new_current_event",
            "hard_block": False, "historical_backtest_block": True})
    events["events"].sort(key=lambda r: r["source_id"])
    events["hard_blockers"] = [r["source_id"] for r in events["events"] if r["hard_block"]]
    events["complete_current_research_allowed"] = events["complete_current_research_allowed"] and not events["hard_blockers"]
    previous = extension.get("revision_comparison", {}).get("previous_forward")
    revision = compare(previous, forward) if previous else {"status": "no_prior_model_supplied", "rows": []}
    anchor = extension.get("financial_packets", {}).get("anchor", {}).get("values", {})
    normalization = anchor.get("fx_gain_twd", 0) + anchor.get("fair_value_gain_twd", 0)
    if snapshot["instrument"]["stock_id"] == "3231" and revision["rows"]:
        for row in revision["rows"]:
            prior_value = row["previous_inputs"].get("nonoperating_twd")
            current_value = row["current_inputs"].get("nonoperating_twd")
            if prior_value is None or current_value is None:
                continue
            change = current_value - prior_value
            row["normalization_review"] = {
                "source_packet": "anchor", "source_document_ids": extension["financial_packets"]["anchor"]["document_ids"],
                "fx_gain_twd": anchor.get("fx_gain_twd"), "fair_value_gain_twd": anchor.get("fair_value_gain_twd"),
                "nonoperating_change_twd": change,
                "matches_removed_fx_and_fair_value": abs(change - normalization) < 1,
                "interpretation": "Old normalized nonoperating income excluded FX/fair-value gains; new model carries reported net nonoperating income. Assumption normalization change, not issuer restatement or evidence of higher forecast accuracy."}
    eps = extension.get("valuation_eps_originals", [])
    prices = [{"stock_id": snapshot["instrument"]["stock_id"], "date": r["data_date"],
               "close": r["values"]["close"], "captured_at": r["retrieved_at"],
               "source_sha256": r.get("raw_sha256") or fingerprint(r["raw"]),
               "price_basis": "unadjusted_TWD_per_common_share"}
              for r in snapshot["datasets"]["TaiwanStockPrice"]["records"]]
    prices = extension.get("valuation_price_originals", prices)
    pe = historical_pe(prices, eps, snapshot["instrument"]["stock_id"], cutoff)
    projection = plan.get("revenue_projection", {})
    if extension.get("historical_revenue_snapshot"):
        projection = revenue_diagnostic(extension["historical_revenue_snapshot"],
                                        snapshot["instrument"]["stock_id"], cutoff)
    calibration = [{"horizon_months": v["horizon_months"], "n": v["paired_origins"],
                    "mae_twd": v["mae_twd"], "available_at": projection.get("available_at", cutoff),
                    "type": "current_revision_diagnostic_not_PIT_calibration"}
                   for v in projection.get("calibration", {}).values()]
    scored = evaluate(extension.get("forecast_evaluation_inputs", []),
                      snapshot["instrument"]["stock_id"], documents, cutoff)
    backtest = result["extension"].get("backtest", {})
    counts = [{"split": split, "horizon": horizon, "n": len(r["events"]),
               "independent_forward_sample": False, "edge_proven": False}
              for split, horizons in backtest.get("splits", {}).items() for horizon, r in horizons.items()]
    bridge = extension.get("forward_estimate", {}).get("share_bridge", {})
    share_cases = []
    issued = [(p["as_of_date"], p["values"]["issued_shares"])
              for p in extension.get("financial_packets", {}).values()
              if p.get("source_kind") == "issuer_capital" and "issued_shares" in p.get("values", {})]
    issued_shares = max(issued)[1] if issued else None
    for case in forward.get("scenarios", {}).get("base", {}).get("quarters", []):
        shares = case["inputs"]["diluted_weighted_shares"]["value"]
        share_cases.append({"period": case["period"], "assumed_weighted_diluted_shares": shares,
                            "observed_issued_shares": issued_shares,
                            "difference_is_not_proven_future_potential_shares": shares - issued_shares
                            if shares is not None and issued_shares is not None else None,
                            "qualification_status": "UNVERIFIED_future_note_and_recognition_dates_required"})
    growth = []
    monthly = extension.get("financial_packets", {}).get("monthly", {})
    q3 = monthly.get("values", {}).get("revenue_twd")
    for case in forward.get("scenarios", {}).get("base", {}).get("quarters", []):
        value = case["inputs"]["revenue_twd"]["value"]
        if case["period"].endswith("Q3") and q3 and value:
            growth.append({"period": case["period"], "reference_period": "2026-Q3",
                           "reference_revenue_twd": q3, "assumed_revenue_twd": value,
                           "growth_pct": (value / q3 - 1) * 100,
                           "proxy": "official_monthly_revenue_sum_only_not_units_or_ASP",
                           "shipment_ASP_validation": "FAIL_no_independent_volume_price_mix_bridge"})
    summary = [f"公告逐筆狀態：{len(events['events'])} 項；重大或範圍阻擋 {len(events['hard_blockers'])} 項。已知事實研究可讀，完整當前研究仍不宣稱通過。",
               f"關鍵參數 {len(parameters)} 項分開檢查完整性、未來預測證據與校準；已審查不等於準確度通過。",
               f"嚴格同口徑歷史 PE 通過 {pe['verified_points']} 點；現今回補供應商 PE 不當合理退出倍數。",
               "base 是具名條件情境，不是最可能情境；反向 EPS 門檻使用假設 PE，不是市場共識。",
               "歷史回補誤差與前瞻實績分開；尚無足夠可比前瞻實績證明 EPS 或交易策略優勢。"]
    return {"schema_version": 5, "status": "reviewed_with_explicit_gaps", "events": events,
            "parameter_dimensions": parameters, "revision_bridge": revision,
            "dilution_timeline": {"existing_model_share_bridge": bridge,
                                  "quarterly_share_checks": share_cases,
                                  "observed_capital_events": extension.get("capital", {}),
                                  "future_instrument_qualification": "NOT_VERIFIED_requires_new_EPS_note_and_recognition_dates",
                                  "future_share_count_is_fact": False},
            "growth_proxy_checks": growth, "historical_pe": pe,
            "calibration": {"eps_prospective_n": scored["n"], "eps_mae": scored["mae"], "eps_mape": scored["mape"],
                            "revenue_diagnostics": calibration, "status": "FAIL_not_prospectively_calibrated",
                            "revenue_snapshot_sha256": projection.get("source_snapshot_sha256"),
                            "revenue_diagnostic_status": projection["status"],
                            "probabilities": None, "historical_sample_count_is_not_independent": True},
            "strategy_samples": counts, "summary": summary,
            "base_is_most_likely": False, "new_forecast_issued": False}


def render(review, table, historical=False):
    if review.get("status") == "unavailable_no_research_plan":
        return ""
    from html import escape

    out = '<section id="v5-review"'+(' data-scope="historical"' if historical else '')+'><h2>'+(
        'V5：歷史審查基準（非本輪處理狀態）' if historical else 'V5：證據、模型差異與驗證')+'</h2>'
    if historical:
        out += '<p>以下保留原始未去重、未完成原件數值審查時的基準清單；本輪缺件、P0與複核結果以後方原件驗收為準，不把舊計數當新增消息或當前模型狀態。</p>'
    out += ''.join('<p>' + escape(s) + '</p>' for s in review["summary"])
    events = review["events"]["events"]
    out += table(["官方事件／來源", "公布時點", "狀態", "模型影響／原因"],
                 [[r["source_id"], r.get("published_at") or "未知", r["status"],
                   r.get("model_impact", "") + " / " + r["reason"]] for r in events])
    out += table(["季度／情境", "參數", "完整性", "預測證據", "校準", "替代值／EPS 差"],
                 [[p["period"] + '/' + p["scenario"], p["parameter_name"], *p["dimensions"].values(),
                   str([(s["alternative"], round(s["eps_delta_twd"], 4)) for s in p["sensitivity"]])]
                  for p in review["parameter_dimensions"] if p["scenario"] == "base"])
    for r in review["revision_bridge"].get("rows", []):
        if r["scenario"] == "base" and r["steps"]:
            out += '<h3>' + escape(r["period"]) + ' 前後版本歸母差異橋</h3>'
            out += table(["變數", "舊值", "新值", "歸母差額（億元）", "EPS 差"],
                         [[s["driver"], s["old"], s["new"], s["delta"]["parent_profit_twd"] / 1e8,
                           s["delta"]["eps_twd"] if s["delta"]["eps_twd"] is not None else "舊版 EPS 不可用"] for s in r["steps"]])
            normalization = r.get("normalization_review", {})
            if normalization.get("matches_removed_fx_and_fair_value"):
                out += '<p>本差異來自業外常態化選擇：舊版另扣匯兌與評價利益，新版沿用已核對 Q2 淨業外。不是公司重編，也不能證明新版更準；股數與稀釋分子的新增假設分開列示，不補算舊版不存在的 EPS。</p>'
    out += table(["季度", "營收成長假設（%）", "可核對代理", "出貨／ASP 支撐"],
                 [[r["period"], r["growth_pct"], r["proxy"], r["shipment_ASP_validation"]]
                  for r in review["growth_proxy_checks"]])
    out += table(["季度", "假設稀釋股數", "已觀測發行股數", "資格"],
                 [[r["period"], r["assumed_weighted_diluted_shares"], r["observed_issued_shares"], r["qualification_status"]]
                  for r in review["dilution_timeline"]["quarterly_share_checks"]])
    out += '<p>股數算式不等於未來稀釋資格：GDR 實發、轉換完成、可轉債反稀釋與 NCI、融資及回款時間表須按新公告核對；已納入分母的工具不得重加。</p>'
    out += table(["切分", "持有日", "歷史事件數", "前瞻獨立樣本"],
                 [[r["split"], r["horizon"], r["n"], "否"] for r in review["strategy_samples"]])
    out += table(["營收預測距離（月）", "共同回補樣本數", "三方法 MAE（億元）", "資料捕獲可用時點"],
                 [[r["horizon_months"], r["n"], str({k: round(v / 1e8, 3) if v is not None else None for k, v in r["mae_twd"].items()}), r["available_at"]]
                  for r in review["calibration"]["revenue_diagnostics"]])
    out += '<p>上述 MAE 是現今回補版本的 rolling-origin 診斷；樣本重疊、權重在當前重估，不是當年可部署的樣本外績效。EPS 前瞻可比實績 n=' + str(review["calibration"]["eps_prospective_n"]) + '；MAE=' + str(review["calibration"]["eps_mae"]) + '（None 表示不可用）。不據樣本數自行宣稱統計優勢。</p>'
    out += '<p>逐點 PE 口徑、來源可用時間、所有情境參數與歷史樣本：<a href="v5-review.json">完整 V5 驗收</a>。缺件保留不可用，不製造績效或合理價。</p></section>'
    return out
