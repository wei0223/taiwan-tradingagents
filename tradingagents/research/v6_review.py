"""V6 operational resolution, five validation dimensions and one current status table."""

from .bundle import fingerprint
from .output_registry import build as output_registry
from .pit_validation import replay
from .resolution import gates, queue
from .scenarios import timestamp

KINDS = {"observed", "company_guidance", "statistical_estimate", "judgment_assumption", "stress_only"}


def parameters(rows, spec, cutoff):
    result = []
    metadata = spec.get("forward_parameter_metadata", {})
    for row in rows:
        meta = metadata.get(row["scenario"], {}).get(row["period"], {}).get(row["parameter_name"], {})
        kind = meta.get("forecast_kind", "judgment_assumption" if row["validity_status"] == "reviewed" else "stress_only")
        if kind not in KINDS:
            raise ValueError("Unknown V6 forecast input classification")
        if kind == "observed" and (int(row["period"][:4]), int(row["period"][-1])*3) > (int(cutoff[:4]), int(cutoff[5:7])):
            raise ValueError("A future quarter cannot be classified as an observed actual")
        if kind == "observed" and (row.get("assumption_type") != "official" or meta.get("observed_period") != row["period"]):
            raise ValueError("Observed input needs same-period official actual evidence")
        sources = row["source_availability"]
        supported = bool(sources) and all(r.get("sha256") and timestamp(r["known_since"]) <= timestamp(cutoff) for r in sources)
        checks = {"schema_valid": "PASS" if row["value"] is not None and row.get("formula") else "FAIL",
                  "source_supported": "PASS" if supported else "FAIL",
                  "assumption_reviewed": "PASS" if meta.get("reviewed_by") and row["validity_status"] == "reviewed" else "FAIL",
                  "historical_calibrated": "FAIL", "forward_validated": "FAIL"}
        result.append({"scenario": row["scenario"], "period": row["period"], "parameter": row["parameter_name"],
                       "value": row["value"], "classification": kind, "as_of": cutoff,
                       "confidence_basis": meta.get("confidence_basis", row.get("rationale")),
                       "validation": checks, "source_support_scope": "available_anchor_not_verified_future_realization",
                       "sources": sources, "alternatives": row["alternatives"], "sensitivity": row["sensitivity"],
                       "historical_error": row["historical_error"], "base_probability": None})
    return result


def debate_trigger(old, new, documents, cutoff, completed=()):
    from tradingagents.dataflows.taiwan.periods import _document, cutoff_time

    before = {(r["stock_id"], r["period"], r["metric"]): r for r in old}
    changes = []
    thresholds = {"revenue_twd": .05, "eps_twd": .10, "diluted_weighted_shares": .01,
                  "gross_margin": .01, "operating_margin": .01, "tax_rate": .02}
    for row in new:
        key = (row["stock_id"], row["period"], row["metric"])
        prior = before.get(key)
        if (not prior or row["metric"] not in thresholds or not row.get("reviewed_by")
                or row.get("values_reviewed") is not True or not row.get("source_locator")):
            continue
        doc = documents.get(row.get("source_id"), {})
        try:
            _document(doc, row["stock_id"], cutoff_time(cutoff))
        except (ValueError, KeyError, TypeError):
            continue
        if doc.get("sha256") == prior.get("source_sha256") or row.get("source_sha256") != doc.get("sha256"):
            continue
        delta = abs(row["value"]-prior["value"])
        magnitude = delta if row["metric"] in {"gross_margin", "operating_margin", "tax_rate"} else (
            delta/abs(prior["value"]) if prior["value"] else (1 if delta else 0))
        if magnitude >= thresholds[row["metric"]]:
            changes.append({"key": key, "old": prior["value"], "new": row["value"],
                            "source_sha256": doc["sha256"], "magnitude": magnitude})
    identity = fingerprint(changes) if changes else None
    return {"should_trigger": bool(changes) and identity not in completed,
            "change_id": identity, "verified_material_changes": changes,
            "reason": "verified_material_numeric_change" if changes else "no_new_reviewed_material_numbers",
            "automatic_LLM_calls": False, "already_completed": identity in completed if identity else False}


def build(snapshot, result, spec, documents):
    v5 = result.get("v5_review", {})
    if v5.get("status") != "reviewed_with_explicit_gaps":
        return {"status": "unavailable_explicit_plan_required"}
    plan = result["extension"]["research_plan"]
    cutoff = plan["context"]["cutoff_at"]
    work = queue(result["stock_id"], v5["events"]["events"], plan)
    original_review = None
    if spec.get("source_numeric_reviews"):
        from .original_review import audit
        from .resolution import index_rows

        original_review = audit(work, index_rows(documents), documents,
                                spec["source_numeric_reviews"], result["generated_at"])
        if original_review["rejected"]:
            raise ValueError("Original numeric review rejected: "+str(original_review["rejected"][:2]))
        work = original_review["queue"]
    registry = output_registry(snapshot, result.get("forward_estimate", {}), result["generated_at"])
    validation = parameters(v5["parameter_dimensions"], spec, cutoff)
    pit = {"status": "UNAVAILABLE_FROZEN_PLAN_AND_VINTAGES_REQUIRED", "folds": [],
           "completed_input_isolated_folds": 0, "forecast_hit_rate": None}
    if spec.get("pit_evaluation"):
        run = spec["pit_evaluation"]
        if run["plan"].get("stock_id") != result["stock_id"]:
            raise ValueError("PIT evaluation company mismatch")
        pit = replay(run["inputs"], run.get("outcomes", []), run["plan"]["folds"], run["plan"], result["generated_at"])
    trigger = spec.get("debate_change_inputs", {})
    debate = debate_trigger(trigger.get("before", []), trigger.get("after", []), documents,
                            cutoff, trigger.get("completed_change_ids", []))
    estimate = result.get("forward_estimate", {})
    eps_inventory = None
    if spec.get("valuation_latest_reported"):
        from .valuation_audit import eps_inventory as inventory

        eps_inventory = inventory(result["stock_id"], spec["valuation_latest_reported"],
                                  spec.get("valuation_eps_originals", []), result["generated_at"])
    return {"schema_version": 6, "status": "operational_controls_with_explicit_empirical_gaps",
            "resolution_queue": work, "layered_gates": gates(work, result["latest_market_gate"], cutoff),
            "parameters": validation, "current_output_registry": registry, "original_numeric_review": original_review,
            "pit_evaluation": pit, "debate_trigger": debate, "valuation_eps_inventory": eps_inventory,
            "valuation_layers": {
                "vendor_reported_history": {"status": "UNVERIFIED_DENOMINATOR", "source": "extension.json#valuation"},
                "current_reviewed_quarter_sum_price_reference": {"ttm": estimate.get("ttm"), "pe": estimate.get("ttm_pe"),
                    "as_of": cutoff, "price_date": estimate.get("price_date"), "price_basis": estimate.get("price_basis"),
                    "not_historical_PIT": True},
                "PIT_historical_TTM_PE": {"verified_points": v5["historical_pe"]["verified_points"],
                    "source": "v5-review.json#historical_pe", "status": "BLOCKED" if not v5["historical_pe"]["verified_points"] else "SELECTED_POINTS_VERIFIED"},
                "conditional_forward_PE": {"model_version": registry["model_version"], "not_fair_value": True},
                "conditional_price_matrix": {"PE_axes": estimate.get("pe_sensitivity"), "exit_multiple_verified": False},
                "reverse_expectations": {"rows": estimate.get("reverse", []), "PE_is_assumed_not_consensus": True},
            }, "new_forecast_issued": False, "legacy_current_status_removed": True}


def render(review, table):
    if review.get("schema_version") != 6:
        return ""
    from html import escape

    work, registry = review["resolution_queue"], review["current_output_registry"]
    out = '<section id="v6-review"><h2>V6：修復作業與當前模型狀態</h2>'
    out += '<p>以下數量分開計算公告、索引範圍及歷史版本缺口，不能合稱獨立重大訊息。取得原件不等於核對數值影響；沒有以清除警告解除 Gate。</p>'
    out += table(["項目", "狀態"], [[k, v] for k, v in review["layered_gates"].items() if k not in {"unresolved_P0", "explicit_user_waivers"}])
    out += table(["分類計數", "數量"], work["counts"].items())
    out += '<details><summary>P0 處理清單與可執行動作</summary>' + table(
        ["event_id／類型", "公布／觀測", "影響／優先級", "處理動作", "狀態／原因"],
        [[r["event_id"][:12]+" / "+r["record_kind"], str(r["published_at"])+" / "+str(r["observed_at"]),
          r["impact_tag"]+" / "+r["severity"], r["resolution_action"], r["resolution_state"]+" / "+r["reason"]]
         for r in work["items"]]) + '</details>'
    out += '<p>完整識別、來源URL與更正鏈：<a href="resolution-queue.json">修復佇列</a>；<a href="source-repair/resolution-run.json">逐筆處理紀錄</a>。取得後仍須原件數值與版本審查，未獲使用者授權不接受模型自行豁免。</p>'
    numerical = review.get("original_numeric_review")
    if numerical:
        out += '<h3>原件數值複核</h3><p>完成 '+str(numerical["numeric_reviews_completed"])+' 項具名原文數值審查；保留原字串、單位與倍率、影響結論及未完成要求。去重前 '+str(numerical["queue"]["input_count"])+ ' 項、唯一識別後 '+str(numerical["queue"]["canonical_count"])+ ' 項；沒有只按時間合併。</p>'
        out += table(["事件", "審查結論", "狀態", "仍缺"], [[r["event_id"][:12],r["impact_conclusion"],r["resolution_state"],str(r.get("remaining_requirements", []))] for r in numerical["reviewed"]])
    out += '<p>當前輸出：模型 '+escape(registry["model_version"])+'；run '+registry["calculation_run_id"][:16]+'；snapshot '+registry["input_snapshot_id"][:16]+'；source_as_of '+escape(registry["source_as_of"])+ '。每個值與資格見 <a href="current-output-registry.json">單一狀態表</a>。正式／條件資格分開，基準條件不是機率最高。</p>'
    base = [r for r in review["parameters"] if r["scenario"] == "base"]
    out += '<details><summary>每季基準條件：五層參數驗證（全部情境見 JSON）</summary>'+table(
        ["季度／參數", "類別", "schema", "source", "reviewed", "歷史校準", "前瞻驗證"],
        [[r["period"]+" / "+r["parameter"], r["classification"], *r["validation"].values()] for r in base])+'</details>'
    out += '<h3>分開閱讀五種估值口徑</h3>'+table(
        ["口徑", "資格"], [["來源報列歷史倍數", "未驗證分母，不當退出倍數"],
        ["目前資料的四季稀釋 EPS 和／價格參考", "直接季報原件不足則不可用；不是歷史時點驗證"],
        ["歷史點時 TTM PE", str(review["valuation_layers"]["PIT_historical_TTM_PE"]["verified_points"])+" 個通過點"],
        ["情境 Forward PE", "條件計算，不代表低估"], ["EPS×PE 條件價格矩陣", "倍數假設，不是合理價"]])
    eps_inventory = review.get("valuation_eps_inventory")
    if eps_inventory:
        missing = ', '.join(r["period"] for r in eps_inventory["quarters"] if r["status"] != "verified_direct") or '無'
        out += '<p>直接季度稀釋EPS原件 '+str(eps_inventory["verified_count"])+'/4；缺季：'+missing+'。原件足夠重算目前四季和，仍不等於當年版本已驗證。</p>'
    pit = review["pit_evaluation"]
    out += '<h3>真正逐期資料隔離驗收</h3>'+table(["區段", "已知訓練月數", "可計算起點", "無法計算起點", "狀態"],
        [[r["fold"]["name"], r["input_training_months"], len(r["predictions"]), len(r["pending"]), r["status"]] for r in pit.get("folds", [])])
    out += '<p>沒有當年已知數值版本時維持 INPUTS_UNAVAILABLE；兩段有執行檢查不等於兩段回測通過。新凍結模型不回填發布時間，回補 MAE 不算 forecast_hit_rate。</p>'
    out += '<p>獨立辯論觸發：'+escape(review["debate_trigger"]["reason"])+ '；本次未自動呼叫 LLM。逐期輸入、固定權重、MAE／MAPE／sMAPE／偏誤與分位誤差：<a href="v6-review.json">V6 驗收附件</a>。</p></section>'
    return out
