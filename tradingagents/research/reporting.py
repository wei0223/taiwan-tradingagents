"""Portable three-analyst research package with a short decision front and audit appendices."""

import base64
import copy
import hashlib
import html
from datetime import UTC, datetime
from pathlib import Path

from .bundle import build_bundle, fingerprint
from .debate import write_json
from .dynamics import conditional_decision, reward_risk, rolling
from .quality import audit_html, industry_review, source_audit
from .report_view import (
    CSS,
    ROLE_LABELS,
    introduction,
    normalize_notes,
    prepare_sources,
    role_notes,
    sources_section,
)
from .review import review
from .scenarios import check_assumption, forecast


def table(headers, rows):
    return (
        '<div class="scroll" tabindex="0" role="region" aria-label="資料表，可左右捲動"><table><thead><tr>'
        + "".join('<th scope="col">' + html.escape(str(h)) + "</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join(
            "<tr>" + "".join("<td>" + html.escape(str(c)) + "</td>" for c in row) + "</tr>"
            for row in rows
        )
        + "</tbody></table></div>"
    )


def package(
    snapshot,
    output_dir,
    specification=None,
    sources=None,
    analyst_prose=None,
    review_input=None,
    release_path=None,
    actuals=None,
    extension_input=None,
    debate_input=None,
    pdf_font=None,
    official_source_store=None,
    official_source_snapshot=None,
):
    output = Path(output_dir)
    # Review editions must never overwrite a delivered report or a sealed source run.
    protected = ("report.html", "research.json", "release-pointer.json", "snapshot.json")
    if any((output / name).exists() for name in protected):
        raise ValueError("Use a new output directory for every report/review edition")
    if actuals is not None and release_path is None:
        raise ValueError("Actuals require an original sealed release")
    plan_spec = (extension_input or {}).get("research_plan")
    if plan_spec:
        from .knowledge import context
        from .scenarios import timestamp
        from .vintages import verify_receipt_times

        ctx = context(plan_spec["context"], snapshot["instrument"]["stock_id"], snapshot["as_of"])
        review_input = copy.deepcopy(
            review_input or {"stock_id": snapshot["instrument"]["stock_id"]}
        )
        if review_input.get("cutoff_at") and timestamp(review_input["cutoff_at"]) != timestamp(
            ctx["cutoff_at"]
        ):
            raise ValueError("Review and research plan must use the same exact cutoff")
        review_input["cutoff_at"] = ctx["cutoff_at"]
        if ctx["mode"] != "retrospective_diagnostic":
            verify_receipt_times(snapshot, timestamp(ctx["cutoff_at"]))
            verify_receipt_times(review_input or {}, timestamp(ctx["cutoff_at"]))
    if release_path is not None and specification:
        from .publication import read_release

        if fingerprint(specification) != read_release(release_path)["specification_sha256"]:
            raise ValueError("Changed model requires a separate versioned forecast release")
    from .financial_periods import audit as audit_periods, prepare as prepare_periods

    source_snapshot = copy.deepcopy(snapshot)
    snapshot = prepare_periods(snapshot, review_input, extension_input)
    period_audit = audit_periods(source_snapshot, snapshot)
    bundle = build_bundle(snapshot)
    audit = source_audit(snapshot)
    if audit["status"] != "passed":
        raise ValueError("Source audit failed")
    model = forecast(bundle, specification)
    dynamics = rolling(snapshot)
    industry = industry_review(bundle, sources or [])
    recent = dynamics.get("windows", {}).get("20", {})
    groups = recent.get("institutions", {})
    chip_complete = bool(groups) and all(g["coverage_complete"] for g in groups.values())
    decision = conditional_decision(model, dynamics, chip_complete)
    risk_scenarios = []
    if specification and specification.get("decision_assumptions") and model["status"] == "ready":
        config = specification["decision_assumptions"]
        entry = check_assumption(config["entry_price"], bundle, 0)
        boundary = check_assumption(config["invalidation_price"], bundle, 0)
        if not 0 < boundary < entry:
            raise ValueError("Explicit loss boundary must be below entry")
        for name, scenario in model["scenarios"].items():
            target = scenario["scenario_price_twd"]
            risk_scenarios.append(
                {
                    "scenario": name,
                    "assumed_entry": entry,
                    "assumed_loss_boundary": boundary,
                    "conditional_target": target,
                    "metrics": reward_risk(entry, target, boundary)
                    if target is not None and target > entry
                    else None,
                    "status": "positive_reward"
                    if target is not None and target > entry
                    else "no_positive_reward",
                }
            )
        decision["risk_reward"] = risk_scenarios
        decision["risk_reward_reason"] = (
            "Explicit illustrative entry/loss assumptions; not fills or guaranteed exits"
        )
    stock, as_of = bundle["stock_id"], bundle["as_of"]
    notes, missing_notes = normalize_notes(analyst_prose, stock, as_of)
    source_entries, evidence_refs, review_assets = prepare_sources(review_input, stock)
    unresolved_refs = set()
    reviewed = review(stock, as_of, review_input, model, snapshot=snapshot)
    from .extension_view import render as render_extension
    from .research_debate import validate as validate_research_debate
    from .research_extension import build as build_extension

    extra = build_extension(snapshot, reviewed, review_input, dynamics, extension_input)
    debate_review = validate_research_debate(debate_input, stock, snapshot, review_input)
    sealed = None
    if release_path is not None:
        from .publication import read_release

        sealed = read_release(release_path)
        if sealed["stock_id"] != stock:
            raise ValueError("Sealed forecast and daily report company mismatch")
        if specification and fingerprint(specification) != sealed["specification_sha256"]:
            raise ValueError("Changed model requires a separate versioned forecast release")
    numbers = {e["path"]: e["value"] for e in bundle["evidence"].values()}
    price_paths = sorted(p for p in numbers if p.startswith("prices.") and p.endswith(".close"))
    last_price = numbers[price_paths[-1]] if price_paths else None
    result = {
        "schema_version": 1,
        "stock_id": stock,
        "as_of": as_of,
        "bundle_sha256": fingerprint(bundle),
        "source_audit": audit,
        "forecast": model,
        "dynamics": dynamics,
        "industry": industry,
        "decision": decision,
        "review": reviewed,
        "forecast_preservation": {
            "status": "original_release_verified" if sealed else "no_release_supplied",
            "release_sha256": sealed["release_sha256"] if sealed else None,
            "original_issued_at": sealed["issued_at"] if sealed else None,
            "predictions_sha256": fingerprint(sealed["predictions"]) if sealed else None,
            "model_changed": False,
        },
        "extension": extra,
        "debate_review": debate_review,
        "independent_agents_executed": debate_review["independent_agents_executed"],
        "prediction_accuracy": {"status": "pending_future_actuals", "mae": None},
        "strategy_performance": {
            "status": "insufficient_sample",
            "reason": "No sealed comparable prospective signals and resolved outcomes",
        },
        "llm_api_calls": False,
        "orders": False,
    }
    result["generated_at"] = datetime.now(UTC).isoformat()
    result["financial_period_conversion"] = period_audit
    from .financial_periods import render_section

    period_section = render_section(snapshot, period_audit, table)
    registry_section = ""
    if bool(official_source_store) != bool(official_source_snapshot):
        raise ValueError("Official source store and snapshot are required together")
    if official_source_store:
        from tradingagents.dataflows.taiwan.source_store import SourceStore

        from .source_registry import assess

        source_store = SourceStore(official_source_store)
        state = source_store.read_snapshot(official_source_snapshot)
        if state["stock_id"] != stock:
            raise ValueError("Registry company mismatch")
        from .review import _end_of_day
        from .scenarios import timestamp

        report_cutoff = (
            timestamp(review_input["cutoff_at"])
            if (review_input or {}).get("cutoff_at")
            else _end_of_day(as_of)
        )
        if timestamp(state["captured_at"]) > report_cutoff:
            raise ValueError("Registry snapshot after report cutoff")
        registry = assess(source_store, official_source_snapshot, review_input)
        result["official_source_registry"] = registry
        registry_section = (
            '<section id="official-source-registry"><h2>正式來源與可用範圍</h2><p>來源查核時間：'
            + html.escape(registry["source_checked_at"])
            + "。行情資料日仍以上方標示為準。</p>"
        )
        registry_section += table(
            ["判定", "結果"],
            [
                ["目前研究", "已核對事實可用，完整研究仍有缺口"],
                [
                    "本輪原件與已審查財報版本一致",
                    registry["current_facts"][
                        "source_matched_previously_reviewed_financial_numbers"
                    ],
                ],
                [
                    "本輪原件與已審查單季現金流版本一致",
                    registry["current_facts"]["source_matched_reviewed_single_quarter_cash"],
                ],
                [
                    "指定月營收原件",
                    registry["current_facts"]["specified_monthly_revenue_originals_available"],
                ],
                ["歷史嚴格回測", "未通過：缺逐歷史截止時點的當時版本"],
                ["七筆歷史事件／版本對應", "仍待官方可驗證紀錄"],
            ],
        )
        if registry["current_issued_share_reference"]:
            capital = registry["current_issued_share_reference"]
            registry_section += (
                "<p>本次公司基本資料的已發行普通股數為 "
                + f"{capital['shares']:,}"
                + " 股；僅代表捕獲時觀測值，不是季度加權稀釋股數或流通股數。不可用於回填歷史比率。</p>"
            )
        registry_section += '<p>來源拒絕、空白或缺件不否定舊版原件曾披露的數值，也不能證明目前未更正。已審查會計數值、現金回收、募資完成、估值與交易訊號仍各自判定。</p><p><a href="official-sources/assessment.json">來源台帳與逐類缺口</a></p></section>'
    from .publishing import assess as assess_publishing, render as render_publishing

    publication = assess_publishing(
        snapshot, result, extension_input, (review_input or {}).get("documents", {})
    )
    result["publishing_quality"] = publication
    result["publishing_status"] = publication["publishing_status"]
    result["latest_market_gate"] = publication["latest_market_gate"]
    publishing_section = render_publishing(publication, table)
    estimate_section = ""
    if (extension_input or {}).get("forward_estimate"):
        from .forward_estimate import build as build_estimate, render as render_estimate

        estimate = build_estimate(extension_input["forward_estimate"], extra["research_plan"],
                                  publication, (review_input or {}).get("documents", {}), stock)
        result["forward_estimate"] = estimate
        result["forecast_preservation"]["separate_new_model_version"] = estimate.get("model_version")
        estimate_section = render_estimate(estimate, table)
    from .v5_review import build as build_v5, render as render_v5

    result["v5_review"] = build_v5(snapshot, result, extension_input or {},
                                   (review_input or {}).get("documents", {}))
    v5_section = render_v5(result["v5_review"], table)
    from .v6_review import build as build_v6, render as render_v6

    result["v6_review"] = build_v6(snapshot, result, extension_input or {},
                                   (review_input or {}).get("documents", {}))
    if result["v6_review"].get("schema_version") == 6 and result.get("forward_estimate"):
        extra["research_plan"]["current_output_registry"] = result["v6_review"]["current_output_registry"]
        estimate_section = render_estimate(result["forward_estimate"], table,
                                           result["v6_review"]["current_output_registry"])
    v6_section = render_v6(result["v6_review"], table)
    dashboard, extension_appendix = render_extension(extra, debate_review, snapshot, table)
    if not publication["latest_market_gate"]["current_trade_conclusion_allowed"]:
        decision["current_trade_conclusion_allowed"] = False
        decision["publication_status"] = "BLOCKED_STALE_OR_UNVERIFIED_MARKET"
        decision["risk_reward"] = []
        risk_scenarios = []
    result["retrospective_model"] = bool(
        (specification and specification.get("created_at", "")[:10] > as_of)
        or (extra["financial_cases"] and result["generated_at"][:10] > as_of)
    )
    summary = (
        '<section id="verdict"><h2>分項裁決</h2><p>已實現每股獲利、現金持續性、估值與短期交易分開判斷。'
        "沒有證明低估不等於證明高估；會計獲利不等於現金回收或股票總報酬。</p>"
    )
    labels = {
        "accounting_earnings": "已實現會計獲利",
        "cash_sustainability": "現金持續性",
        "valuation": "估值",
        "short_term": "短期交易（五至二十交易日）",
    }
    status_labels = {
        "unavailable": "不可用／待原件",
        "both_increased": "歸母與稀釋 EPS 同比皆增加，支持已實現成長",
        "both_not_increased": "歸母與稀釋 EPS 同比皆未增加",
        "mixed": "歸母與每股傳導分歧",
        "nonnegative_cfo": "單季 CFO 非負；持續性與回款另查",
        "negative_cfo": "單季 CFO 仍負",
        "conditional_prices_only": "僅條件價格，不成立合理價或低估結論",
        "observe": "觀察",
        "candidate_pending_next_open": "條件候選，待下一實際開盤重算；尚非交易訊號",
    }
    summary += table(
        ["獨立問題", "程式裁決", "依據／限制"],
        [
            [
                labels[k],
                "BLOCKED：行情時效未通過" if k == "short_term" and not publication["latest_market_gate"]["current_trade_conclusion_allowed"] else status_labels[v["status"]],
                "只供歷史情境測量" if k == "short_term" and not publication["latest_market_gate"]["current_trade_conclusion_allowed"] else v.get("reason", v.get("interpretation", "固定規則，未證明統計優勢")),
            ]
            for k, v in reviewed["axes"].items()
        ],
    )
    summary += (
        "<p>不可用代表無法裁決，不是看空、零值或中性。完整數值與來源見審查附錄；本流程不下單。</p>"
    )
    summary += '<p><a href="review.json">分項計算與條件</a> · <a href="review-input.json">原件、公布／取得時間與版本</a></p>'
    summary += table(
        ["檢核面向", "結果"],
        [
            [
                {
                    "reviewed_valuation_model": "原件核對與情境模型",
                    "price_confirmation": "價格結構確認",
                    "complete_institutional_window": "法人視窗完整性",
                }[k],
                "已滿足" if v else "未滿足",
            ]
            for k, v in decision["checks"].items()
        ],
    )
    summary += "<p>短期觀察範圍為後續五至二十個市場交易日；新增正式財報、資料更正或價格結構失效時重新評估。這是觀察框架，未模擬成交或持倉。</p>"
    if model["status"] == "ready":
        summary += "<h2>EPS 與價格情境</h2><p>以下為明示假設的條件模型，並非公司指引或已驗證合理價；無同業基準時，不將假設本益比稱作合理本益比。採稀釋股數，與歷史基本 EPS 分開。</p>"
        rows = []
        for name, label in [("bear", "悲觀"), ("base", "基準"), ("bull", "樂觀")]:
            r = model["scenarios"][name]
            eps = r["next_four_quarter_eps_sum"]
            annual = r["annual"].get(str(int(as_of[:4]) + 1), {})
            rows.append(
                [
                    label,
                    f"{eps:.2f}",
                    f"{annual.get('eps_sum', 0):.2f}"
                    if annual.get("quarters") == 4
                    else "全年未覆蓋",
                    f"{r['assumed_pe']:.2f}",
                    f"{r['scenario_price_twd']:.2f}"
                    if r["scenario_price_twd"] is not None
                    else "虧損不適用",
                    f"{last_price / eps:.2f}" if last_price is not None and eps > 0 else "不可用",
                ]
            )
        summary += table(
            [
                "情境",
                "未來四季 EPS 合計",
                "下一年度季度 EPS 合計近似",
                "假設 PE",
                "條件價格（元）",
                "現價／四季估算 EPS",
            ],
            rows,
        )
        summary += "<p>年度欄為季度稀釋 EPS 加總近似，非經查核年度加權稀釋 EPS。來源報列本益比未核實分母口徑，因此不標為 TTM PE。情境價格不是目標價；報酬風險比若有列示，也僅依明示進場與失效假設計算，並非可執行報價或保證停損。</p>"
    else:
        summary += '<h2>前瞻 EPS 與估值：依資格分級</h2><p>可計算的條件 EPS 與缺項逐季列於上方發布資格表；完整前瞻估值未通過時不給合理價或低估排名。<a href="#publishing-quality">查看條件試算與阻擋原因</a>。</p>'
    summary += "<h2>什麼會改變各項判斷？</h2>" + table(
        ["面向", "可重算條件"], [[labels[r["axis"]], r["rule"]] for r in reviewed["conditions"]]
    )
    summary += "<p>比較基準取各公司同季原件；不使用緯創或其他公司的固定利潤率、股數或估值倍數。回款證據不足持續不可用。</p>"
    details = ""
    if risk_scenarios:
        summary += "<h2>條件式報酬與風險</h2><p>以下使用明示的示意進場與失效價格，並非實際委託或保證停損；跳空、成本與滑價仍可能改變結果。條件未滿足時維持觀察。</p>"
        summary += table(
            ["情境", "示意進場", "示意失效價", "條件目標", "潛在報酬", "價格風險", "報酬風險比"],
            [
                [
                    r["scenario"],
                    f"{r['assumed_entry']:.2f}",
                    f"{r['assumed_loss_boundary']:.2f}",
                    f"{r['conditional_target']:.2f}"
                    if r["conditional_target"] is not None
                    else "不可用",
                    f"{r['metrics']['reward_pct']:.2f}%" if r["metrics"] else "無正向報酬",
                    f"{(1 - r['assumed_loss_boundary'] / r['assumed_entry']) * 100:.2f}%",
                    f"{r['metrics']['reward_risk']:.2f}" if r["metrics"] else "不適用",
                ]
                for r in risk_scenarios
            ],
        )
    if model["status"] == "ready":
        summary += (
            "<p>情境建立時間："
            + html.escape(str(specification.get("created_at", "未提供")))
            + "。本次為封存資料的回顧式條件建模，並非原分析日已封存的預測，不得作歷史預測績效。</p>"
        )
    summary += "</section>"
    analyst_sections = ""
    roles = [
        ("fundamental", "基本面分析師"),
        ("technical", "技術面分析師"),
        ("institutional", "籌碼面分析師"),
    ]
    for role, label in roles:
        body = role_notes(role, notes[role], evidence_refs, unresolved_refs)
        if role == "fundamental":
            body += "<p>營收規模、利潤率與現金轉換分開判斷；未取得的產品組合與同業資料明列缺口。歷史成長不直接保證估值吸引力。</p>"
            earnings = reviewed["axes"]["accounting_earnings"]
            if earnings["status"] != "unavailable":
                current, prior = earnings["current"], earnings["prior"]
                body += "<h3>已核對單季會計獲利</h3>" + table(
                    [
                        "項目",
                        current["period_start"] + " 至 " + current["period_end"],
                        prior["period_start"] + " 至 " + prior["period_end"],
                    ],
                    [
                        [
                            label,
                            f"{current[key] / scale:,.{digits}f}" + suffix,
                            f"{prior[key] / scale:,.{digits}f}" + suffix,
                        ]
                        for key, label, scale, digits, suffix in [
                            ("parent_profit_twd", "歸母淨利（億元）", 1e8, 2, ""),
                            ("eps_twd", "精算稀釋 EPS（元）", 1, 6, ""),
                            ("diluted_numerator_twd", "稀釋分子（億元）", 1e8, 2, ""),
                            ("diluted_weighted_shares", "稀釋加權股數（百萬股）", 1e6, 3, ""),
                            ("operating_margin", "營益率", 0.01, 2, "%"),
                            ("parent_margin", "歸母率", 0.01, 2, "%"),
                        ]
                    ],
                )
                body += (
                    "<p>來源定位："
                    + html.escape(current["locator"] + "；" + prior["locator"])
                    + "。這是列示期間的已實現獲利，不是未來 EPS 預測。</p>"
                )
            cash = reviewed["axes"]["cash_sustainability"]
            observed = cash.get("ytd_observation", {})
            if observed.get("status") == "available":
                body += "<h3>現金流口徑</h3>" + table(
                    ["項目", "金額／期間"],
                    [
                        [
                            "累計期間（不可稱單季）",
                            observed["period_start"] + " 至 " + observed["period_end"],
                        ],
                        ["累計 CFO（億元）", f"{observed['values']['cfo_twd'] / 1e8:,.2f}"],
                        ["累計營收（億元）", f"{observed['values']['revenue_twd'] / 1e8:,.2f}"],
                        [
                            "單季 CFO（億元）",
                            f"{cash['single_quarter']['cfo_twd'] / 1e8:,.2f}"
                            if "single_quarter" in cash
                            else "不可用：待版本銜接",
                        ],
                        ["回款確認", "不可用：不能從餘額或 CFO 推定"],
                    ],
                )
            compared = cash.get("same_YTD_comparison", {})
            if compared.get("status") == "available":
                body += table(
                    ["同期間同比檢查", "程式計算"],
                    [
                        ["CFO 絕對變化（億元）", f"{compared['cfo_change_twd'] / 1e8:,.2f}"],
                        ["CFO／營收比變化（百分點）", f"{compared['ratio_change_pp']:,.2f}"],
                    ],
                )
                body += "<p>絕對資金需求與每元營收的現金流出可能朝不同方向變化；兩者都要看，不自行加回正常化 CFO。</p>"
            if reviewed["capital_events"]:
                body += "<h3>募資與股數核對</h3>" + table(
                    ["狀態", "核對／待補事項"],
                    [
                        [r["status"], r.get("reason", "已核對發行與附註；不重複加計股數")]
                        for r in reviewed["capital_events"]
                    ],
                )
            if model["status"] == "ready":
                body += "<h3>可追溯假設表</h3>" + table(
                    [
                        "情境／季度",
                        "營收假設（億元）",
                        "毛利率",
                        "營益率",
                        "稅率",
                        "加權稀釋股數（百萬股）",
                    ],
                    [
                        [
                            n + "/" + r["period"],
                            f"{r['revenue_twd']['value'] / 1e8:.2f}",
                            f"{r['gross_margin']['value'] * 100:.2f}%",
                            f"{r['operating_margin']['value'] * 100:.2f}%",
                            f"{r['tax_rate']['value'] * 100:.2f}%",
                            f"{r['weighted_diluted_shares']['value'] / 1e6:.3f}",
                        ]
                        for n, s in specification["scenarios"].items()
                        for r in s["quarters"]
                    ],
                )
                body += table(
                    ["季度", "悲觀稀釋 EPS", "基準稀釋 EPS", "樂觀稀釋 EPS"],
                    [
                        [
                            r["period"],
                            *[
                                f"{model['scenarios'][n]['quarters'][i]['eps_twd']:.2f}"
                                for n in ["bear", "base", "bull"]
                            ],
                        ]
                        for i, r in enumerate(model["scenarios"]["base"]["quarters"])
                    ],
                )
                body += table(
                    [
                        "情境",
                        "營業利益率增加一個百分點：四季 EPS 變動",
                        "PE 增加一倍數：條件價格變動",
                    ],
                    [
                        [
                            n,
                            f"{r['sensitivity_operating_margin_plus_one_pp_eps']:.2f}",
                            r["sensitivity_per_one_pe_turn_twd"],
                        ]
                        for n, r in model["scenarios"].items()
                    ],
                )
            body += "<h3>產業與競爭力證據</h3>" + table(
                ["來源類型", "論點", "營收／利潤率傳導", "失效條件"],
                [
                    [s["classification"], s["claim"], s["transmission"], s["invalidation"]]
                    for s in industry["sources"]
                ],
            )
            body += (
                "<p>尚缺面向："
                + html.escape("、".join(industry["missing_dimensions"]))
                + "。缺少相同期別及單位的同業數值時，不產生同業估值排名或宣稱競爭優勢已量化。</p>"
            )
        else:
            rows = []
            for w, r in dynamics.get("windows", {}).items():
                if r["status"] != "available":
                    rows.append([w, "缺資料", r["reason"]])
                elif role == "technical":
                    rows.append(
                        [
                            w,
                            f"報酬 {r['return_pct']:.2f}%；MA 單日變動 {r['ma_one_session_change']:.3f}；ATR {r['atr_simple_mean']:.3f}",
                            f"窗口區間突破：{'已觀測' if r['breakout_previous_window_high'] else '未觀測'}；單日假突破規則：{'符合' if r['false_breakout_one_session_rule'] else '未符合'}",
                        ]
                    )
                else:
                    rows.append(
                        [
                            w,
                            "；".join(
                                (
                                    {
                                        "foreign": "外資",
                                        "investment_trust": "投信",
                                        "dealer": "自營商",
                                    }.get(n, n)
                                    + ": "
                                    + (
                                        f"{g['window_net_shares'] / 1000:,.2f} 張"
                                        if g["window_net_shares"] is not None
                                        else "不可用"
                                    )
                                )
                                for n, g in r["institutions"].items()
                            ),
                            r["start"] + " 至 " + r["end"],
                        ]
                    )
            body += table(["視窗（交易日）", "程式計算", "觀測範圍／判定"], rows)
            body += "<p>技術型態為明示機械規則，並非已驗證勝率；ATR 使用真實波幅簡單平均。缺日不補零，法人淨額不代表全部籌碼或因果證明。</p>"
        analyst_sections += (
            '<section class="analyst" id="analyst-'
            + role
            + '"><h2>'
            + ROLE_LABELS[role]
            + "</h2>"
            + body
            + "</section>"
        )
    result["reading_review"] = {
        "template_version": 2,
        "analyst_roles": [role for role, _ in roles],
        "missing_editorial_fields": missing_notes,
        "unresolved_evidence": sorted(unresolved_refs),
        "status": "complete_structure"
        if not any(missing_notes.values()) and not unresolved_refs
        else "editorial_review_required",
        "semantic_truth_verified_by_template": False,
        "visual_browser_review": "not_performed_by_template",
    }
    if sealed is not None:
        from .tracking import read_tracking, track

        tracking = read_tracking(track(release_path, actuals or [], output / "tracking"))
        result["forecast_tracking"] = tracking
        summary += "<h2>原預測封存與追蹤</h2>" + table(
            ["項目", "核對結果"],
            [
                ["原發布時間（不改寫）", sealed["issued_at"]],
                ["原發布雜湊", sealed["release_sha256"]],
                ["原預測紀錄數", len(sealed["predictions"])],
                ["初次公告主評分", tracking["primary_initial_vintage_results"]["status"]],
                ["另選版本診斷", tracking["results"]["status"]],
                ["原封存模型修改", "無；本版新條件模型另建版本" if result.get("forward_estimate") else "無；若改模型另建新版本"],
            ],
        )
    # Audit payloads stay in portable attachments, never inline in the reading page.
    details += '<section class="machine-audit no-print"><h2>完整證據與計算</h2><p><a href="research.json">計算結果</a> · <a href="publishing-quality.json">發布驗收與假設</a> · <a href="delivery-manifest.json">SHA256 清單</a> · <a href="evidence.zip">完整證據包</a>。獨立 Agent 原稿及既有辯論保存在附件，本輪未重新執行辯論。</p></section>'
    reading_gap = (
        '<p class="reading-gap">本版尚缺分析師裁決欄位或有未綁定引用；'
        "結構檢查不代表研究論點已被驗證，詳見 reading-review.json。</p>"
        if result["reading_review"]["status"] != "complete_structure"
        else ""
    )
    page = (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + stock
        + " 研究升級版</title><style>"
        + CSS
        + "</style></head><body><main>"
        + introduction(stock, as_of, result["generated_at"], dynamics.get("latest_session"))
        + reading_gap
        + period_section
        + publishing_section
        + estimate_section
        + v6_section
        + v5_section
        + dashboard
        + registry_section
        + summary
        + analyst_sections
        + extension_appendix
        + sources_section(source_entries, review_input)
        + details
        + "<footer>資料版本 "
        + fingerprint(snapshot)[:12]
        + ' · <a href="reading-review.json">閱讀結構檢查</a> · <a href="research.json">完整計算</a>'
        + "。此版面不代表模型或交易策略已有績效證明。</footer></main></body></html>"
    )
    links = audit_html(page)
    if links["status"] != "passed":
        raise ValueError("Nonportable report")
    if result["v6_review"].get("schema_version") == 6:
        from .output_registry import audit_presentation

        result["v6_presentation_audit"] = audit_presentation(page, result["v6_review"]["current_output_registry"])
    result["html_audit"] = links
    output.mkdir(parents=True, exist_ok=True)
    if official_source_store:
        source_store.export(official_source_snapshot, output / "official-sources" / "ledger")
        write_json(output / "official-sources" / "assessment.json", registry)
    for path, content in review_assets.items():
        target = output / path
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(content)
    write_json(output / "snapshot.json", snapshot)
    write_json(output / "source-snapshot.json", source_snapshot)
    write_json(output / "period-conversion.json", period_audit)
    write_json(output / "analysts.json", analyst_prose or {})
    write_json(output / "reading-review.json", result["reading_review"])
    write_json(output / "review.json", reviewed)
    write_json(output / "review-input.json", review_input or {})
    write_json(
        output / "source-ledger.json",
        [
            {
                "id": key,
                **{
                    k: v
                    for k, v in doc.items()
                    if k not in {"content_base64", "rows", "raw_response_utf8", "raw_rows"}
                },
            }
            for key, doc in (review_input or {}).get("documents", {}).items()
        ],
    )
    write_json(output / "extension.json", extra)
    write_json(output / "extension-input.json", extension_input or {})
    write_json(output / "debate-review.json", debate_review)
    write_json(output / "debate-input.json", debate_input or {})
    write_json(output / "bundle.json", bundle)
    if sealed is not None:
        write_json(output / "forecast-preservation.json", result["forecast_preservation"])
        sealed_copy = output / "sealed-releases" / stock / Path(release_path).name
        sealed_copy.parent.mkdir(parents=True, exist_ok=True)
        sealed_copy.write_bytes(Path(release_path).read_bytes())
    if model["status"] == "ready":
        documents = output / "documents"
        documents.mkdir(exist_ok=True)
        for doc in model["assumptions"]["basis_review"]["documents"]:
            content = base64.b64decode(doc["content_base64"], validate=True)
            extension = ".pdf" if content.startswith(b"%PDF") else ".bin"
            (documents / (doc["sha256"] + extension)).write_bytes(content)
    write_json(output / "research.json", result)
    observation = {"recorded_at": result["generated_at"], "stock_id": stock,
                   "data_as_of": as_of, "input_snapshot_sha256": fingerprint(source_snapshot),
                   "reviewed_snapshot_sha256": fingerprint(snapshot),
                   "research_sha256": fingerprint(result),
                   "forward_model_sha256": result.get("forward_estimate", {}).get("model_sha256"),
                   "new_forecast_issued": False,
                   "kind": "immutable_daily_research_observation_not_prediction_release"}
    observation["record_sha256"] = fingerprint(observation)
    write_json(output / "research-observation.json", observation)
    write_json(output / "publishing-quality.json", publication)
    write_json(output / "v5-review.json", result["v5_review"])
    write_json(output / "v6-review.json", result["v6_review"])
    if result["v6_review"].get("original_numeric_review"):
        write_json(output / "original-numeric-review.json", result["v6_review"]["original_numeric_review"])
    if result["v6_review"].get("schema_version") == 6:
        write_json(output / "resolution-queue.json", result["v6_review"]["resolution_queue"])
        write_json(output / "current-output-registry.json", result["v6_review"]["current_output_registry"])
    if result.get("forward_estimate"):
        write_json(output / "forward-estimate.json", result["forward_estimate"])
    (output / "report.html").write_text(page, encoding="utf-8", newline="\n")
    write_json(
        output / "manifest.json",
        {
            "stock_id": stock,
            "as_of": as_of,
            "bundle_sha256": fingerprint(bundle),
            "research_sha256": fingerprint(result),
            "html_sha256": hashlib.sha256(page.encode()).hexdigest(),
            "review_sha256": reviewed["review_sha256"],
            "review_input_sha256": fingerprint(review_input or {}),
            "portable_assets_sha256": {
                path: hashlib.sha256((output / path).read_bytes()).hexdigest()
                for path in [
                    *review_assets,
                    "snapshot.json",
                    "analysts.json",
                    "reading-review.json",
                ]
            },
        },
    )
    from .delivery import seal

    if pdf_font is not None:
        from .paper_report import render_pdf

        render_pdf(output, pdf_font)
    if result["v6_review"].get("schema_version") == 6:
        from .resolution import execute, index_rows

        docs = (review_input or {}).get("documents", {})
        dispositions = execute(result["v6_review"]["resolution_queue"], index_rows(docs), docs,
                               output/"source-repair"/"attempts", now=result["generated_at"], budget=0)
        write_json(output/"source-repair"/"resolution-run.json", dispositions)
    seal(output)
    return result
