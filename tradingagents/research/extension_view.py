"""Compact research dashboard and model/debate appendices; no generated numbers."""

import html


def chart(values, label, suffix=""):
    """Self-contained SVG, with the exact input series exported separately in JSON."""
    values = [(str(k), float(v)) for k, v in values if v is not None]
    if len(values) < 2:
        return "<p>圖表不可用：不足兩筆同口徑觀測。</p>"
    lo, hi = min(v for _, v in values), max(v for _, v in values)
    span = hi - lo or max(abs(hi) * 0.1, 1)
    points = " ".join(
        f"{35 + i / (len(values) - 1) * 550:.1f},{140 - (v - lo) / span * 110:.1f}"
        for i, (_, v) in enumerate(values)
    )
    escape = html.escape
    return (
        '<figure><svg viewBox="0 0 620 180" role="img" style="width:100%;max-height:220px" '
        'xmlns="http://www.w3.org/2000/svg"><title>'
        + escape(label)
        + "</title><desc>"
        + escape(f"{values[0][0]} 至 {values[-1][0]}；最低 {lo:,.2f}，最高 {hi:,.2f}{suffix}")
        + '</desc><path d="M35 20V145H590" fill="none" stroke="#a8b9c5"/>'
        + '<polyline fill="none" stroke="#176e82" stroke-width="2.5" points="'
        + points
        + '"/>'
        + f'<text x="35" y="170" font-size="12">{escape(values[0][0])}</text>'
        + f'<text x="500" y="170" font-size="12">{escape(values[-1][0])}</text>'
        + f'<text x="40" y="16" font-size="12">{lo:,.2f} - {hi:,.2f} {escape(suffix)}</text>'
        + "</svg><figcaption>"
        + escape(label)
        + "</figcaption></figure>"
    )


def render(extra, debate, snapshot, table):
    esc = html.escape
    out = '<section id="research-dashboard"><h2>研究摘要儀表板</h2><p>好公司、好成長、好價格分開回答。財務試算不是正式預測，反向估值不是已驗證市場共識。</p>'
    from .plan_view import render as render_plan

    out += render_plan(extra.get("research_plan", {}), table)
    cash = extra["cash_quality"]
    ratio = cash["cfo_to_parent_profit_descriptive"]
    out += table(
        ["核心檢查", "結果"],
        [
            ["公司研究規則", extra["company_profile"]["label"]],
            ["財務模型", "已附條件計算" if extra["financial_cases"] else "缺可核對驅動因子"],
            [
                "單季 CFO／歸母（描述用）",
                f"{ratio:.2f} 倍；合併 CFO 與歸母範圍不同，不跨業評分"
                if ratio is not None
                else "不可用",
            ],
            [
                "獨立多空交叉質詢",
                "一輪完成；原稿與前後立場保留"
                if debate["independent_agents_executed"]
                else "未執行",
            ],
            ["統計信心區間／策略優勢", "尚未建立；不把情境範圍當機率區間"],
            [
                "資料品質未通過項目",
                "產品量價因果、遠期股數、完整回款橋接、前瞻估值分母及前瞻樣本外績效仍須補證",
            ],
        ],
    )
    revenue = [
        (r["period"], r["revenue_twd"] / 1e8) for r in snapshot.get("monthly_revenue", [])[-18:]
    ]
    out += chart(revenue, "已公布月營收趨勢（資料截止日版本）", "億元")
    if debate["independent_agents_executed"]:
        ruling = debate["judgment"]
        out += "<h3>核心分歧與裁決變化</h3>" + table(
            ["問題", "研究主管裁決"],
            [
                ["先前立場", ruling["before"]],
                ["覆核後立場", ruling["after"]],
                ["較有力論點", ruling["stronger_case"]],
                ["改變判斷的證據", ruling["evidence_that_changed_judgment"]],
            ],
        )
    out += '<p><a href="evidence.zip">下載完整證據包（含數據、計算、原件、Agent原稿及檢查）</a> · <a href="extension.json">新增模型與診斷</a></p></section>'
    appendix = (
        '<section id="model-appendix"><h2>財務、現金與估值模型</h2><h3>公司專屬研究問題</h3><p>'
    )
    appendix += (
        esc("；".join(extra["company_profile"]["drivers"]))
        + "。研究規則不提供共用利潤率、成長率或倍數。</p>"
    )
    for case in extra["financial_cases"]:
        c = case["calculation"]
        appendix += (
            "<h3>"
            + esc(case.get("name", case["period"]))
            + "</h3><p>條件機械試算，非正式預測；假設逐項見 extension-input.json。無校準機率或信心區間。</p>"
        )
        appendix += "<p>" + esc(case["cost_classification"]) + "</p>"
        breakeven = case.get("gross_margin_to_match_reference_eps", {})
        if breakeven.get("status") == "conditional_sensitivity":
            appendix += (
                "<p>其餘假設凍結時，要維持已核對季度 EPS，所需毛利率約 "
                + f"{breakeven['required_gross_margin'] * 100:.2f}%"
                + "；不是預測門檻。</p>"
            )
        appendix += table(
            ["傳導步驟", "程式計算"],
            [
                [label, f"{c[key] / scale:,.3f}"]
                for key, label, scale in (
                    ("revenue_twd", "營收（億元）", 1e8),
                    ("gross_profit_twd", "毛利（億元）", 1e8),
                    ("opex_twd", "營業費用（億元）", 1e8),
                    ("operating_profit_twd", "營業利益（億元）", 1e8),
                    ("pretax_profit_twd", "稅前利益（億元）", 1e8),
                    ("tax_twd", "所得稅（億元）", 1e8),
                    ("parent_profit_twd", "歸母（億元）", 1e8),
                    ("diluted_weighted_shares", "稀釋加權股數（百萬股）", 1e6),
                    ("eps_twd", "稀釋 EPS（元）", 1),
                )
            ],
        )
    detail = cash["details"]
    appendix += "<h3>現金流品質：營運資金、資本支出與資金來源</h3>"
    if detail["status"] == "available":
        v = detail["values"]
        appendix += (
            "<p>下表為 "
            + detail["period_start"]
            + " 至 "
            + detail["period_end"]
            + " 累計期間，不能稱單季。應收／存貨／應付使用現金流量表的帶符號影響，不以餘額差冒充收款。</p>"
        )
        appendix += table(
            ["項目", "億元"],
            [
                [name, f"{value / 1e8:,.2f}"]
                for name, value in [
                    ("CFO", v["cfo_twd"]),
                    ("應收現金影響", v["ar_cash_effect_twd"]),
                    ("存貨現金影響", v["inventory_cash_effect_twd"]),
                    ("應付現金影響", v["ap_cash_effect_twd"]),
                    ("折舊", v["depreciation_twd"]),
                    ("PPE 現金支出", v["capex_ppe_twd"]),
                    ("CFO 減 PPE 支出", detail["fcf_ppe_only_twd"]),
                    ("CFO 減 PPE 與無形支出", detail["fcf_ppe_and_intangibles_twd"]),
                    ("籌資現金淨額", v["financing_cashflow_twd"]),
                ]
            ],
        )
        days = detail["days"]
        if days["status"] == "available_proxy":
            appendix += table(
                ["兩點平均週轉代理", "天"],
                [
                    [k, f"{days[key]:.2f}"]
                    for k, key in (
                        ("DSO", "dso"),
                        ("DIO", "dio"),
                        ("DPO（COGS代理）", "dpo_cogs_proxy"),
                        ("CCC（代理）", "ccc_cogs_proxy"),
                    )
                ],
            )
        appendix += "<p>自由現金流採明示定義，未偷偷涵蓋預付設備、租賃或併購；付款條件與完整現金／非現金橋接尚未核對，回款證據繼續不可用。</p>"
    else:
        appendix += "<p>缺少已核對的細項與期初餘額；單季 CFO 比率不能取代完整品質分析。</p>"
    val = extra["valuation"]
    appendix += "<h3>歷史倍數與反向估值</h3>"
    if "reported_history" in val:
        hist = val["reported_history"]
        appendix += table(
            ["已取得", "結果"],
            [
                ["來源報列 PE 觀測數", hist["count"]],
                ["來源報列 PE 中位數", hist["median"]],
                ["固定樣本期間", str(hist.get("first_date")) + " 至 " + str(hist.get("last_date"))],
                ["樣本指紋", hist.get("sample_sha256", "舊版未保存")],
                ["未來十二月 PE", val["forward"]["status"]],
                ["已驗證 TTM PE", val["trailing"]["status"]],
            ],
        )
        version = val.get("version_comparison", {})
        if version.get("attribution"):
            appendix += "<h3>PE跨版本差異歸因</h3>" + table(
                ["項目", "結果"], [[k, str(v)] for k, v in version["attribution"].items()]
            )
        appendix += table(
            ["假設 PE", "現價倒推所需 EPS", "相對可驗證基準的成長"],
            [
                [
                    f"{r['assumed_pe']:.2f}",
                    f"{r['required_eps_twd']:.2f}",
                    f"{r['required_growth_vs_reference_pct']:.2f}%"
                    if r["required_growth_vs_reference_pct"] is not None
                    else "分母不足，暫不計算",
                ]
                for r in val["reverse"]
            ],
        )
        appendix += "<p>倍數軸為來源報列歷史分位數的敏感度，分母未核實，不能稱歷史 Forward PE 或合理退出倍數。倒推 EPS 是等式條件，並非投資人實際預期；不將單季 EPS 乘四冒充未來十二月估計。</p>"
    appendix += (
        "<h3>短期訊號與籌碼標準化</h3><p>"
        + extra["trade_measurement_label"]
        + "。風報比不是勝率；5／10／20日的成本後重播與樣本限制另存 extension.json。</p>"
    )
    replay = extra["backtest"]
    if not replay.get("splits"):
        appendix += "<p>本版回測不可延長：" + esc(replay.get("reason", "缺完整資料或固定規則")) + "。舊事件保存在證據附件；樣本不足，不宣稱策略優勢。</p>"
    if replay.get("splits"):
        appendix += table(
            ["區間／期限", "事件數", "成本後勝率", "淨報酬中位數", "最差單筆標記收盤回撤"],
            [
                [
                    split + "/" + horizon,
                    r["count"],
                    f"{r['net_win_rate'] * 100:.1f}%"
                    if r.get("net_win_rate") is not None
                    else "樣本不足，僅列單筆",
                    f"{r['return_distribution']['p50'] * 100:.2f}%"
                    if r["return_distribution"]["p50"] is not None
                    else "樣本不足",
                    f"{r['worst_trade_marked_close_drawdown'] * 100:.2f}%"
                    if r["count"]
                    else "不可用",
                ]
                for split, rows in replay["splits"].items()
                for horizon, r in rows.items()
            ],
        )
        for split, rows in replay["splits"].items():
            for horizon, r in rows.items():
                if r.get("aggregate_statistics_suppressed") and r["events"]:
                    appendix += table(
                        ["區間／期限", "訊號日／進場日", "單筆成本後報酬"],
                        [
                            [
                                split + "/" + horizon,
                                e["signal_date"] + " / " + e["entry_date"],
                                f"{e['net_return'] * 100:.2f}%",
                            ]
                            for e in r["events"]
                        ],
                    )
    appendix += table(
        ["視窗／法人", "淨額／窗口成交量", "淨額／可核對流通股數"],
        [
            [
                window + "/" + role,
                f"{r['net_to_volume_pct']:.3f}%"
                if r.get("net_to_volume_pct") is not None
                else "不可用",
                f"{r['net_to_free_float_pct']:.3f}%"
                if r.get("net_to_free_float_pct") is not None
                else "不可用：不用稀釋加權股數代替",
            ]
            for window, groups in extra["normalized_institutions"].items()
            for role, r in groups.items()
        ],
    )
    appendix += "<p>歷史重播為現今回補還原價的回顧診斷；保留區間是本次明訂，不能宣稱未看過的前瞻樣本外績效，也沒有統計優勢證明。</p></section>"
    if debate["independent_agents_executed"]:
        appendix += '<section id="cross-examination"><h2>獨立多空交叉質詢紀錄</h2>'
        for role, label in (("bull", "多方"), ("bear", "空方")):
            opening, reply = debate["openings"][role], debate["replies"][role]
            appendix += (
                "<h3>"
                + label
                + "</h3><p>原立場："
                + esc(opening["position"])
                + "</p><p>反駁後："
                + esc(reply["position_after"])
                + "</p>"
            )
            appendix += table(
                ["回應論點", "評估", "理由及證據", "改變立場"],
                [
                    [
                        r["claim_id"],
                        r["assessment"],
                        r["reason"],
                        "是" if r["changed_my_position"] else "否",
                    ]
                    for r in reply["changes"]
                ],
            )
        appendix += '<p><a href="debate-input.json">原始開場、反駁、風險與裁決（含逐項引用）</a>。信心為未校準質性判斷；不同情境的跨度不代表統計信心區間。</p></section>'
    return out, appendix
