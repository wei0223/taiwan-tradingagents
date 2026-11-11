"""Investor reading layer; full evidence and system diagnostics stay in audit.html."""

import html
import re

from .report_view import CSS, ROLE_LABELS
from .research_questions import analyst_paragraphs, conclusion_parts, display_prose, flow_comparison


def number(value, scale=1, digits=2):
    return "未取得" if value is None else f"{value / scale:,.{digits}f}"


def render(data, notes, snapshot, table):
    reader = data["reader_research"]
    editorial, reverse, market = reader["editorial"], reader["reverse"], reader["market"]
    esc = html.escape

    def prose(value):
        value = esc(display_prose(value))
        return re.sub(
            r"\[([A-Za-z][A-Za-z0-9_-]*)\]",
            lambda m: '<a href="audit.html">[' + m[1] + "]</a>",
            value,
        )

    out = (
        '<header class="report-header"><span class="eyebrow">TAIWAN TRADINGAGENTS · 單次研究</span><h1>'
        + esc(data["stock_id"])
        + " 投資研究報告</h1>"
    )
    out += (
        "<p>研究日 "
        + esc(data["as_of"])
        + " · 行情至 "
        + esc(str(data["dynamics"].get("latest_session")))
        + " · 實際產生 "
        + esc(data["generated_at"][:19])
        + " UTC</p>"
    )
    out += (
        '<p class="edition-note">本次單次研究，不發布正式預測、不評分、不建立追蹤。'
        + esc(editorial.get("data_note", "資料期間按來源分列，行情日期不是報告產生日期。"))
        + "</p></header>"
    )
    out += '<nav><a href="#conclusion">研究結論</a><a href="#industry">成長與競爭</a><a href="#price">現價要求</a><a href="#market">市場結構</a><a href="#debate">核心分歧</a><a href="#sources">查閱來源</a></nav>'
    conclusions = conclusion_parts(
        editorial.get("verdict", notes["fundamental"]["stronger_case"] or notes["fundamental"]["position"])
    )
    out += '<section id="conclusion"><h2>先看結論</h2><div class="analyst-position"><ul>'
    out += "".join("<li>" + prose(part) + "</li>" for part in conclusions[:3]) + "</ul></div>"
    conditions = data.get("debate_review", {}).get("judgment", {}).get("revision_conditions", [])
    if conditions and data.get("workflow_review"):
        out += "<p><strong>優先改判條件</strong> " + prose(conditions[0]) + "</p>"
    if reader.get("latest_verified_quarter_end"):
        out += "<p>最新已核合併單季財務期間截至 " + esc(reader["latest_verified_quarter_end"]) + "。</p>"
    from .reader_research import post_close_ruling_text

    post_close_ruling = post_close_ruling_text(data)
    if post_close_ruling:
        out += "<p><strong>盤後短線裁決</strong> " + prose(post_close_ruling) + "</p>"
    if reader.get("short_term_check") and data.get("fixed_pipeline"):
        from .reader_research import short_term_text
        out += "<p><strong>固定短線觀察</strong> " + prose(short_term_text(reader["short_term_check"])) + "</p>"
    if len(conclusions) > 3:
        out += "<details><summary>展開裁決詳述</summary>" + "".join("<p>" + prose(part) + "</p>" for part in conclusions[3:]) + "</details>"
    out += table(
        ["問題", "研究判斷"],
        [
            ["長期獲利與價值", notes["fundamental"]["position"]],
            ["短期交易觀察", notes["technical"]["position"]],
            ["籌碼供需", notes["institutional"]["position"]],
        ],
    )
    for item in editorial.get("new_insights", []):
        out += "<p><strong>" + esc(item["title"]) + "</strong> " + prose(item["text"]) + "</p>"
    from .fixed_reading import debate_rows, ruling_rows
    rulings = ruling_rows(data)
    if rulings:
        out += '<h3>四項研究裁決</h3>' + table(["判斷軸", "裁決", "依據"], rulings)
        out += '<p>研究裁決不自動解除財務數值、估值或歷史績效資格；計算資格另見稽核版。</p>'
    out += (
        "<p>"
        + ('<a href="report.pdf">下載閱讀版 PDF</a> · ' if data.get("reader_pdf_requested") else "")
        + '<a href="evidence.zip">下載完整可攜證據包</a> · <a href="audit.html">開發與來源稽核版</a></p></section>'
    )
    if "debate_impact" in data:
        from .debate_impact import summary_line

        out += ('<h2>辯論前後差異</h2><p>' + esc(summary_line(data["debate_impact"]))
                + ' · <a href="debate-impact.json">立場及反駁紀錄</a></p>')
    if "reasoning_audit" in data:
        from .reasoning_audit import display_rows as reasoning_rows

        out += ('<h2>重大主張審查</h2><p>' + esc(data["reasoning_audit"]["status"])
                + ' · <a href="reasoning-audit.json">推論稽核</a>'
                + ' · <a href="claim-validation.json">逐項主張</a></p>'
                + table(["主張", "狀態", "審查理由"], reasoning_rows(data["claim_validation"])))
    if data.get("local_research_archive"):
        out += ('<p><a href="forecast-record.json">本機研究封存</a>'
                ' · <a href="forecast-manifest.json">封存完整性清單</a></p>')
    if "decision_conditions" in data:
        from .decision_conditions import display_rows as condition_rows

        out += ('<h2>條件式交易研究</h2><p><a href="decision-conditions.json">觸發、失效及成本明細</a></p>'
                + table(["項目", "狀態", "條件"], condition_rows(data["decision_conditions"])))
    if "valuation_scenarios" in data:
        from .valuation_horizons import display_rows as valuation_rows

        scenarios, implied, times = valuation_rows(data["valuation_scenarios"], data["horizon_analysis"])
        out += ('<h2>估值與時間尺度</h2><p><a href="valuation-scenarios.json">三情境及倍數依據</a>'
                ' · <a href="horizon-analysis.json">時間尺度</a></p>'
                + table(["尺度", "狀態", "依據"], times)
                + table(["情境", "四季EPS合計（元）", "條件價格"], scenarios))
        if implied:
            out += table(["假設倍數", "現價所需EPS（元）", "較已公布四季變化（%）", "基準EPS減所需EPS（元）"], implied)
    if "expectation_gap" in data:
        from .market_expectations import display_rows as gap_rows

        out += ("<h2>資料品質與市場預期差</h2><p>資料可用性："+data["data_quality"]["status"]
                + ' · <a href="data-quality-report.json">資料來源與缺口</a>'
                + ' · <a href="expectation-gap.json">預期差明細</a></p>'
                + table(["指標", "狀態", "實績減共識／缺口"], gap_rows(data["expectation_gap"])))
    if "public_eps" in data:
        from .public_eps import section as consensus_section
        out += consensus_section(data["public_eps"], table)
    out += '<section id="industry"><h2>基本面分析師｜成長如何轉成每股獲利</h2>'
    for paragraph in notes["fundamental"]["paragraphs"]:
        out += "<p>" + prose(paragraph) + "</p>"
    cards = editorial.get("industry_cards", [])
    if cards:
        out += "<h3>公司專屬競爭命題</h3>" + table(
            ["證據", "獲利傳導與判斷", "失效條件"],
            [
                [
                    c["fact"] + "（" + "、".join(c["source_ids"]) + "）",
                    c["transmission"],
                    c["failure"],
                ]
                for c in cards
            ],
        )
    peers = editorial.get("peers", [])
    if peers:
        out += (
            "<h3>同季同口徑比較</h3><p>以下均為"
            + esc(peers[0]["period_start"] + " 至 " + peers[0]["period_end"])
            + "單季合併、TWD；不同產品結構的差異不自動成為合理估值倍數。</p>"
        )
        out += table(
            ["公司／定位", "營收億元", "毛利率", "營益率", "可比性限制"],
            [
                [
                    p["name"],
                    number(p["revenue_twd"], 1e8),
                    number(p["gross_profit_twd"] / p["revenue_twd"] * 100) + "%",
                    number(p["operating_profit_twd"] / p["revenue_twd"] * 100) + "%",
                    p["comparability"],
                ]
                for p in peers
            ],
        )
    out += (
        "<h3>基本面改判條件</h3><ul>"
        + "".join("<li>" + prose(x) + "</li>" for x in notes["fundamental"]["conditions"])
        + "</ul></section>"
    )
    out += '<section id="price"><h2>現價要求什麼條件？</h2>'
    if reverse["status"] == "conditional_equations_only":
        out += (
            "<p>四季參考 EPS "
            + number(reverse["base_eps"])
            + " 元；營收合計 "
            + number(reverse["base_revenue_twd"], 1e8)
            + " 億元，營收加權毛利率 "
            + number(reverse["base_gross_margin_pct"])
            + "%。這是明示假設的參考，不是市場共識或最可能預測。</p>"
        )
        out += table(
            [
                "假設PE",
                "現價所需EPS",
                "只變營收：四季億元／變動",
                "只變毛利：所需率",
                "只變股數：分母變動",
            ],
            [
                [
                    number(r["assumed_pe"]),
                    number(r["required_eps"]),
                    number(r["required_revenue_twd"], 1e8)
                    + "／"
                    + number(r["revenue_change_pct"])
                    + "%",
                    number(r["required_weighted_gross_margin_pct"]) + "%",
                    number(r["share_change_pct"]) + "%",
                ]
                for r in reverse["rows"]
            ],
        )
        out += "<p>每欄各自只改一項，其餘條件固定；不能將同一列三欄同時套用。股數只是分母敏感度，減少股數不等於可執行庫藏股或減資方案；PE軸未獨立驗證，不能稱市場實際隱含預期。</p>"
        out += (
            "<p>"
            + prose(
                editorial.get(
                    "price_interpretation",
                    "依公司實績、成本與股數證據評估達成難度，不能只從數學等式判定便宜。",
                )
            )
            + "</p>"
        )
        cases = data["extension"]["financial_cases"]
        out += "<h3>四季獲利傳導參考</h3>" + table(
            ["季別", "營收億元", "毛利億元", "營益億元", "歸母億元", "稀釋EPS"],
            [
                [
                    c["period"],
                    *[
                        number(c["calculation"][k], 1e8)
                        for k in [
                            "revenue_twd",
                            "gross_profit_twd",
                            "operating_profit_twd",
                            "parent_profit_twd",
                        ]
                    ],
                    number(c["calculation"]["eps_twd"]),
                ]
                for c in cases
            ],
        )
        out += "<h3>什麼變數最會改變EPS？</h3>" + table(
            ["單一壓力條件", "四季條件EPS", "相對參考差額"],
            [[r["name"], number(r["eps"]), number(r["delta_eps"])] for r in reverse["shocks"]],
        )
        scenarios = editorial.get(
            "valuation_scenarios", [{"name": "持平參考", "eps": reverse["base_eps"]}]
        )
        out += "<h3>EPS × PE 條件價格矩陣</h3>" + table(
            ["條件情境／EPS"] + [number(r["assumed_pe"]) + "倍" for r in reverse["rows"]],
            [
                [
                    s["name"] + "／" + number(s["eps"]),
                    *[number(s["eps"] * r["assumed_pe"]) for r in reverse["rows"]],
                ]
                for s in scenarios
            ],
        )
        out += "<p>情境EPS與PE皆有假設，價格不是目標價，也沒有情境機率；先前封存預測保持原值。</p>"
    elif reverse["status"] == "conditional_eps_only":
        out += "<p>" + esc(reverse["price_date"]) + " 收盤 " + number(reverse["price"]) + " 元。倍數為示意假設，所得為現價所需 EPS。</p>"
        out += table(["假設本益比", "現價所需 EPS（元／股）"],
                     [[number(row["assumed_pe"]), number(row["required_eps"])] for row in reverse["rows"]])
        out += "<p>營收與毛利率門檻須等合格四季模型。公開年度 EPS 保留原期間及口徑。</p>"
    else:
        out += "<p>缺少合格價格或模型，倒推暫不可用。</p>"
    if data.get("fixed_pipeline"):
        from .technical_chart import section
        # The package is passed only in memory to presentation, not generated by the LLM.
        out += section(data["_technical_package"], data["fixed_pipeline"], table)
    out += '</section><section id="market"><h2>技術面與籌碼面｜同一時間軸的供需</h2>'
    flow = flow_comparison(market)
    if flow:
        evidence = next((item for item in data.get("workflow_review", {}).get("calculations", {}).get("cross_evidence_result", [])
                         if item["method"] == "aligned_flow_20"), None)
        if evidence:
            out += '<p><a href="#' + html.escape(evidence["evidence_id"]) + '">查看同窗下跌率與樣本數</a></p>'
        else:
            out += '<p class="analyst-position"><strong>' + prose(flow["summary"]) + '</strong></p>'
    if market["series"]:
        series = market["series"]
        out += timeline(series)
        out += (
            "<p>"
            + prose(
                editorial.get(
                    "market_interpretation",
                    "同軸圖描述價格與淨流量共現，不能識別資金動機或建立因果關係。",
                )
            )
            + "</p>"
        )
        out += table(
            ["當日收盤所在區間", "交易日", "外資淨額（張）", "投信淨額（張）"],
            [
                [
                    r["label"] + " " + number(r["low"]) + "–" + number(r["high"]),
                    r["days"],
                    number(r["foreign"], 1000),
                    number(r["investment_trust"], 1000),
                ]
                for r in market["price_bins"]
            ],
        )
        trust_note = (
            f"投信淨買的 {market['trust_buy_days']} 日中，有 {market['trust_buy_down_days']} 日股價仍下跌。"
            if market["trust_buy_days"] is not None
            else "投信分類缺漏，停止計算買超日與下跌日比率。"
        )
        out += (
            "<p>區間按觀察窗收盤價三分位切分；是淨額發生時的收盤區，不是成交成本或法人持倉成本。"
            + trust_note
            + "缺少分類的日子保留缺值，不補零。</p>"
        )
    for role in ["technical", "institutional"]:
        out += (
            "<h3>"
            + ROLE_LABELS[role]
            + "</h3>"
        )
        for paragraph in analyst_paragraphs(notes[role]):
            out += "<p>" + prose(paragraph) + "</p>"
        out += (
            "<ul>"
            + "".join("<li>" + prose(x) + "</li>" for x in notes[role]["conditions"])
            + "</ul>"
        )
    out += '</section><section id="debate"><h2>只保留會改變結論的分歧</h2>'
    focus = editorial.get("debate_focus", [])
    if focus:
        out += table(
            ["核心分歧", "多方／空方條件", "EPS影響與裁決", "驗證或改判條件"],
            [[r["issue"], r["positions"], r["impact"], r["test"]] for r in focus],
        )
    else:
        out += (
            "<p>"
            + prose(
                data["debate_review"]
                .get("judgment", {})
                .get("stronger_case", "本次未執行獨立辯論，不將舊辯論當新執行。")
            )
            + "</p>"
        )
    out += "<p>完整開場、逐項反駁與前後立場留在證據包。正文不重複雙方共同接受的限制；有數字影響也不代表該情境更可能。</p></section>"
    exchanges = debate_rows(data)
    if exchanges:
        out += '<section><details><summary>展開本次真實逐項交叉反駁</summary>' + table(["方向", "原主張", "對方回應", "立場結果"], exchanges) + '</details></section>'
    out += (
        '<section id="sources"><h2>資料缺口與可查證來源</h2><p>'
        + prose(
            editorial.get(
                "material_unknowns", "產品毛利、未來股數與合理倍數仍須各自證據；缺資料不視為看空。"
            )
        )
        + "</p>"
    )
    if data.get("fixed_pipeline"):
        out += '<p><a href="company-evidence.html">逐筆查閱公司原件、數值、期間與PDF頁碼</a> · <a href="company-package.json">制式公司資料</a></p>'
    for key, source in editorial.get("sources", {}).items():
        out += (
            '<p id="source-'
            + esc(key)
            + '"><strong>'
            + esc(key)
            + "</strong> "
            + esc(source["stock_id"])
            + ' · <a href="reader-sources/'
            + esc(source["sha256"])
            + '.pdf">已封裝原件</a> · <a href="'
            + esc(source["source_url"], quote=True)
            + '">官方網址</a> · PDF頁 '
            + esc(", ".join(map(str, source["pages"])))
            + " · 公布 "
            + esc(str(source.get("published_at") or "未知"))
            + " · 捕獲 "
            + esc(source["captured_at"])
            + "</p>"
        )
    out += '<details class="no-print"><summary>來源、計算與系統檢查</summary><p><a href="audit.html">完整稽核版</a> · <a href="reader-research.json">本版計算與圖表資料</a> · <a href="delivery-manifest.json">檔案校驗清單</a> · <a href="evidence.zip">完整證據包</a></p></details></section>'
    out += "<footer>單次研究完成後結束；不下單。條件估值不是合理價，描述性市場分析不是策略績效。</footer>"
    return (
        '<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + esc(data["stock_id"])
        + " 研究閱讀版</title><style>"
        + CSS
        + "</style></head><body><main>"
        + out
        + "</main></body></html>"
    )


def timeline(series):
    """Stacked synchronized panels, with separate units and a shared session axis."""
    panels = [
        ("close", "收盤價（元）", False),
        ("volume", "成交量（萬股）", True),
        ("foreign", "外資淨額（張）", True),
        ("investment_trust", "投信淨額（張）", True),
    ]
    out = '<figure><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 620" role="img" style="width:100%"><title>同20個交易日：價格、成交量、外資與投信</title>'
    for j, (key, label, bars) in enumerate(panels):
        scale = 10000 if key == "volume" else 1000 if key in ["foreign", "investment_trust"] else 1
        vals = [r[key] / scale for r in series if r[key] is not None]
        top = j * 145 + 28
        if not vals:
            out += f'<text x="55" y="{top}" font-size="13">{label}：資料不足，保留缺值</text>'
            continue
        low = min([0, *vals]) if bars else min(vals)
        high = max([0, *vals])
        span = high - low or 1

        def y(v, top=top, low=low, span=span):
            return top + 98 - (v - low) / span * 90

        out += f'<text x="55" y="{top - 8}" font-size="13">{label}；{low:,.1f}～{high:,.1f}</text><path d="M55 {top + 102}H790" stroke="#b6c4ce"/>'
        points = []
        for i, row in enumerate(series):
            x = 65 + i * 710 / max(1, len(series) - 1)
            if row[key] is None:
                continue
            value = row[key] / scale
            if bars:
                color = "#8b9cac" if key == "volume" else "#b5594b" if value >= 0 else "#227d88"
                out += f'<rect x="{x - 7:.1f}" y="{min(y(value), y(0)):.1f}" width="14" height="{max(0.6, abs(y(value) - y(0))):.1f}" fill="{color}"/>'
            else:
                points.append(f"{x:.1f},{y(value):.1f}")
        if points:
            out += (
                '<polyline points="'
                + " ".join(points)
                + '" fill="none" stroke="#173f5b" stroke-width="2.5"/>'
            )
    out += (
        '<text x="55" y="610" font-size="12">'
        + html.escape(series[0]["date"])
        + '</text><text x="680" y="610" font-size="12">'
        + html.escape(series[-1]["date"])
        + "</text></svg><figcaption>四個面板共用相同交易日位置，各自保留單位；紅為淨買、藍綠為淨賣，收盤價不作還原。缺值不畫成零。</figcaption></figure>"
    )
    return out
