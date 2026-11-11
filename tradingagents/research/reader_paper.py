"""Reader PDF from the same research JSON; engineering details remain in audit.html."""

import html
import json
from pathlib import Path

from .reader_view import number
from .research_questions import analyst_paragraphs, conclusion_parts, display_prose, flow_comparison


def render_pdf(directory, font_path):
    from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Table, TableStyle

    root = Path(directory)
    data = json.loads((root / "research.json").read_text(encoding="utf-8"))
    notes = json.loads((root / "analysts.json").read_text(encoding="utf-8"))
    reader = data["reader_research"]
    ed = reader["editorial"]
    rev = reader["reverse"]
    market = reader["market"]
    pdfmetrics.registerFont(TTFont("ReaderCJK", str(font_path), subfontIndex=0))
    styles = {
        k: ParagraphStyle(
            k,
            fontName="ReaderCJK",
            fontSize=size,
            leading=leading,
            spaceAfter=gap,
            wordWrap="CJK",
            keepWithNext=keep,
        )
        for k, size, leading, gap, keep in [
            ("body", 10, 15, 6, False),
            ("small", 8.5, 12, 5, False),
            ("title", 21, 28, 12, True),
            ("h2", 14, 20, 9, True),
            ("h3", 11, 16, 6, True),
        ]
    }

    def p(value, style="body"):
        return Paragraph(html.escape(display_prose(value)), styles[style])

    def table(headers, rows, widths=None):
        t = Table(
            [[p(c, "small") for c in row] for row in [headers, *rows]],
            colWidths=widths or [180 * mm / len(headers)] * len(headers),
            repeatRows=1,
            hAlign="LEFT",
        )
        t.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7eff4")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#d0dce3")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        return t

    conclusions = conclusion_parts(ed.get("verdict", notes["fundamental"]["stronger_case"]))
    story = [
        p(data["stock_id"] + " 投資研究報告", "title"),
        p(
            "研究日 "
            + data["as_of"]
            + "｜行情 "
            + str(data["dynamics"].get("latest_session"))
            + "｜實際產生 "
            + data["generated_at"][:19]
            + " UTC",
            "small",
        ),
        p(ed.get("data_note", "依資料日分析；不代表即時報價。"), "small"),
        p("研究結論", "h2"),
        *(p("• " + part) for part in conclusions[:3]),
        p("本次單次研究，不發布正式預測、不評分、不建立追蹤；條件價格不是合理價。", "small"),
    ]
    if data.get("reasoning_audit", {}).get("status") == "limited":
        story.append(p("推論審查受限。逐項爭議與待審主張見 claim-validation.json。", "small"))
    conditions = data.get("debate_review", {}).get("judgment", {}).get("revision_conditions", [])
    if conditions and data.get("workflow_review"):
        story.append(p("優先改判條件｜" + conditions[0], "small"))
    if reader.get("latest_verified_quarter_end"):
        story.append(p("最新已核合併單季財務期間截至 " + reader["latest_verified_quarter_end"] + "。", "small"))
    from .reader_research import post_close_ruling_text

    post_close_ruling = post_close_ruling_text(data)
    if post_close_ruling:
        story.append(p("盤後短線裁決｜" + post_close_ruling, "small"))
    if reader.get("short_term_check") and data.get("fixed_pipeline"):
        from .reader_research import short_term_text
        story.append(p("固定短線觀察｜" + short_term_text(reader["short_term_check"]), "small"))
    for item in ed.get("new_insights", []):
        story += [p(item["title"], "h3"), p(item["text"])]
    from .fixed_reading import debate_rows, ruling_rows
    rulings = ruling_rows(data)
    if rulings:
        story += [p("四項研究裁決", "h2"), table(["判斷軸", "裁決", "依據"], rulings, [30*mm, 20*mm, 130*mm]), p("研究裁決不自動解除財務數值、估值或歷史績效資格；計算資格另見稽核版。", "small")]
    if data.get("workflow_review"):
        from .eight_stage import (
            DIMENSION_LABELS,
            evidence_label,
            is_grouped_ma_evidence,
            phase_label,
        )
        workflow = data["workflow_review"]
        story += [p("八階段研究推導", "h2"),
                  table(["階段", "研究狀態"], [[str(r["number"]) + " " + r["name"], phase_label(r)] for r in workflow["phases"]], [111 * mm, 69 * mm]),
                  p("格式通關不等於事實語意、真實派工或交易優勢已證實；檔案交付另見workflow-delivery.json。", "small")]
        stances = {"bullish": "偏多", "neutral": "中立", "bearish": "偏空", "uncertain": "不確定", "unavailable": "不可用"}
        story.append(p("三面向初步裁決" + ("（補查前）" if workflow["active_revision"] else ""), "h3"))
        for ruling in workflow["dimension_rulings"]:
            story.append(p(DIMENSION_LABELS[ruling["dimension"]] + "｜" + stances[ruling["stance"]] + "：" + ruling["reason"] + "；分歧：" + ruling["key_disagreement"]))
        for face in workflow["core"]["faces"]:
            label = face.get("reasoning_label", "待審")
            story += [p(DIMENSION_LABELS[face["dimension"]] + "｜核心主張｜" + label, "h3"), p(face["claim"] + "；" + face["reason"])]
            trend = workflow["calculations"]["trend"] if face["dimension"] == "technical" else None
            if trend:
                story.append(p(trend["summary"] + trend["details"], "small"))
            for item in face["items"]:
                if trend and is_grouped_ma_evidence(item, trend):
                    continue
                story.append(p({"primary": "主要", "support": "補充", "counter": "反面"}[item["role"]] + "：" + evidence_label(item) + "；相關來源群組：" + item["correlation_group"], "small"))
            for limit in face.get("evidence_limitations", []):
                story.append(p("資料限制｜" + limit["reason"] + " " + evidence_label(limit["item"]), "small"))
        story.append(p("跨面向研究與補查", "h3"))
        calculations = workflow["calculations"]
        for item in calculations["cross_evidence_result"]:
            story.append(p(item["evidence_id"] + "｜" + item["measurement"]["summary"], "small"))
        if calculations["flow"]:
            story.append(p("法人原始數值以股保存。每 1,000 股換算 1 張，上列淨額摘要取整張（半值取偶），圖表可保留小數。", "small"))
        for row in calculations["answers"]:
            story.extend([p(row["question"], "h3"), p(row["answer"]),
                          p("、".join(row["evidence_ids"]) + " " + row["limit"], "small")])
        if calculations["answers"] and not workflow["calculations_in_final_prompts"]:
            story.append(p("這些計算是在重出版時補做，原辯論使用當時封存的輸入。", "small"))
        effects = {"strengthen": "增強", "weaken": "削弱", "unchanged": "維持", "indeterminate": "未定"}
        for row in workflow.get("cross_review", {}).get("decision", {}).get("responses", []):
            story.append(p("裁決｜" + effects[row["effect"]] + "｜" + row["evidence_id"] + "｜" + row["reason"], "small"))
        answered = {row["issue_id"] for row in calculations["answers"]}
        for relation in workflow["cross"]["relations"]:
            if relation["id"] in answered:
                continue
            story.append(p(" × ".join(DIMENSION_LABELS[d] for d in relation["dimensions"]) + "：" + relation["explanation"] + "；" + relation["impact"]))
        decisions = workflow["supplement"]["decisions"]
        story.append(p("補查沒有新增查詢。" if not decisions else "；".join(k + "：" + v["status"] + "／" + v["reason"] for k, v in decisions.items()), "small"))
    if "debate_impact" in data:
        from .debate_impact import summary_line

        story += [p("辯論前後差異", "h2"), p(summary_line(data["debate_impact"]), "small")]
    if "decision_conditions" in data:
        from .decision_conditions import display_rows as condition_rows

        story += [p("條件式交易研究", "h2"),
                  table(["項目", "狀態", "條件"], condition_rows(data["decision_conditions"]))]
    if "valuation_scenarios" in data:
        from .valuation_horizons import display_rows as valuation_rows

        scenarios, implied, times = valuation_rows(data["valuation_scenarios"], data["horizon_analysis"])
        story += [p("估值與時間尺度", "h2"), table(["尺度", "狀態", "依據"], times),
                  table(["情境", "四季EPS合計（元）", "條件價格"], scenarios)]
        if implied:
            story.append(table(["假設倍數", "現價所需EPS（元）", "較已公布四季變化（%）", "基準EPS減所需EPS（元）"], implied))
    if "expectation_gap" in data:
        from .market_expectations import display_rows as gap_rows

        story.append(p("資料品質與市場預期差", "h2"))
        story.append(p("資料可用性：" + data["data_quality"]["status"], "small"))
        story.append(table(["指標", "狀態", "實績減共識／缺口"], gap_rows(data["expectation_gap"])))
    if "public_eps" in data:
        from .public_eps import display_rows
        consensus = data["public_eps"]
        story.append(p("FactSet 共識預估EPS（公開轉引）", "h2"))
        if consensus["estimates"]:
            story.append(table(["財務年度", "EPS中位數（元/股）", "分析師人數", "報導日期", "來源"], display_rows(consensus)))
            for url in dict.fromkeys(item["source_url"] for item in consensus["estimates"]):
                story.append(p("公開EPS原文（僅採年度中位數）：" + url, "small"))
            story.append(p(consensus["limitation"], "small"))
        else:
            story.append(p("未取得可核對的公開EPS中位數，不以模型試算補位。"))
    story += [p("基本面分析師｜成長與競爭力", "h2")]
    for paragraph in notes["fundamental"]["paragraphs"]:
        story.append(p(paragraph))
    if ed.get("industry_cards"):
        story += [
            table(
                ["公司證據", "獲利傳導／失效條件"],
                [
                    [
                        c["fact"] + " [" + ",".join(c["source_ids"]) + "]",
                        c["transmission"] + "；失效：" + c["failure"],
                    ]
                    for c in ed["industry_cards"]
                ],
                [70 * mm, 110 * mm],
            )
        ]
    peers = ed.get("peers", [])
    if peers:
        story += [
            p("同季單季合併比較", "h3"),
            p(
                peers[0]["period_start"]
                + "至"
                + peers[0]["period_end"]
                + "；產品組合不同，不直接移植PE。",
                "small",
            ),
            table(
                ["公司", "營收億元", "毛利率", "營益率", "可比限制"],
                [
                    [
                        r["name"],
                        number(r["revenue_twd"], 1e8),
                        number(r["gross_profit_twd"] / r["revenue_twd"] * 100) + "%",
                        number(r["operating_profit_twd"] / r["revenue_twd"] * 100) + "%",
                        r["comparability"],
                    ]
                    for r in peers
                ],
                [25 * mm, 25 * mm, 23 * mm, 23 * mm, 84 * mm],
            ),
        ]
    story += [p("現價要求：從EPS回到營收與成本", "h2")]
    if rev["status"] == "conditional_equations_only":
        story += [
            p(
                "四季參考EPS "
                + number(rev["base_eps"])
                + "元；四季營收 "
                + number(rev["base_revenue_twd"], 1e8)
                + "億元。各欄只改一項，其餘固定，不可同時套用。"
            ),
            table(
                ["PE軸", "所需EPS", "只變營收：億元／變動", "只變毛利：所需率", "只變股數：變動"],
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
                    for r in rev["rows"]
                ],
                [22 * mm, 25 * mm, 58 * mm, 38 * mm, 37 * mm],
            ),
            p(ed.get("price_interpretation", "倍數未驗證，所需股數不是增減資建議；不是市場共識。")),
            table(
                ["壓力條件", "四季EPS", "差額"],
                [[r["name"], number(r["eps"]), number(r["delta_eps"])] for r in rev["shocks"]],
                [110 * mm, 35 * mm, 35 * mm],
            ),
        ]
        cases = data["extension"]["financial_cases"]
        story += [
            p("四季條件傳導", "h3"),
            table(
                ["季別", "營收億", "毛利億", "營益億", "歸母億", "稀釋EPS"],
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
            ),
        ]
        scenarios = ed.get("valuation_scenarios", [{"name": "持平參考", "eps": rev["base_eps"]}])
        story += [
            p("EPS × PE條件價格矩陣", "h3"),
            table(
                ["情境／EPS"] + [number(r["assumed_pe"]) + "倍" for r in rev["rows"]],
                [
                    [
                        s["name"] + "／" + number(s["eps"]),
                        *[number(s["eps"] * r["assumed_pe"]) for r in rev["rows"]],
                    ]
                    for s in scenarios
                ],
            ),
            p("無校準情境機率，不改舊封存預測；單季EPS或年度替代值不拿來填TTM。", "small"),
        ]
    elif rev["status"] == "conditional_eps_only":
        story.extend([
            p(rev["price_date"] + " 收盤 " + number(rev["price"]) + " 元。倍數為示意假設，所得為現價所需 EPS。"),
            table(["假設本益比", "現價所需 EPS（元／股）"],
                  [[number(row["assumed_pe"]), number(row["required_eps"])] for row in rev["rows"]]),
            p("營收與毛利率門檻須等合格四季模型。公開年度 EPS 保留原期間及口徑。", "small"),
        ])
    else:
        story.append(p("缺少合格價格或模型，倒推暫不可用。"))
    story += [p("技術與籌碼｜價格、成交量、法人同軸", "h2")]
    if data.get("fixed_pipeline"):
        from .technical_chart import drawing
        technical = json.loads((root / "technical-package.json").read_text(encoding="utf8"))
        story.append(p("固定指標與K線：程式計算", "h3"))
        if technical["status"] == "ready":
            from reportlab.platypus import KeepTogether

            charts = [drawing(technical), drawing(technical, weekly=True)]
            for chart in charts:
                chart.scale(0.88, 0.88)
                chart.width *= 0.88
                chart.height *= 0.88
                chart.hAlign = "CENTER"
            story.append(KeepTogether(charts))
            story.append(p("完整固定參數、1000日指標序列及61項型態在technical-package.json；沒有LLM補算。", "small"))
        else:
            story.append(p("固定歷史或依賴驗收未通過，停止指標與K線；沒有用舊指標或LLM替代。"))
    series = market["series"]
    if series:
        flow = flow_comparison(market)
        d = Drawing(510, 420)
        for j, (key, label, scale, bars) in enumerate(
            [
                ("close", "收盤價（元）", 1, False),
                ("volume", "成交量（萬股）", 10000, True),
                ("foreign", "外資淨額（張）", 1000, True),
                ("investment_trust", "投信淨額（張）", 1000, True),
            ]
        ):
            vals = [r[key] / scale for r in series if r[key] is not None]
            bottom = 320 - j * 98
            if not vals:
                d.add(
                    String(30, bottom + 78, label + "：資料不足", fontName="ReaderCJK", fontSize=9)
                )
                continue
            lo = min([0, *vals]) if bars else min(vals)
            hi = max([0, *vals])
            span = hi - lo or 1

            def y(v, bottom=bottom, lo=lo, span=span):
                return bottom + 8 + (v - lo) / span * 62

            d.add(
                String(
                    30,
                    bottom + 78,
                    label + " " + number(lo) + "～" + number(hi),
                    fontName="ReaderCJK",
                    fontSize=9,
                )
            )
            d.add(Line(30, bottom + 6, 495, bottom + 6, strokeColor=colors.HexColor("#c5d4dd")))
            points = []
            for i, row in enumerate(series):
                if row[key] is None:
                    continue
                x = 37 + i * 450 / max(1, len(series) - 1)
                v = row[key] / scale
                if bars:
                    d.add(
                        Rect(
                            x - 4,
                            min(y(v), y(0)),
                            8,
                            max(0.5, abs(y(v) - y(0))),
                            fillColor=colors.HexColor(
                                "#8b9cac" if key == "volume" else "#b5594b" if v >= 0 else "#227d88"
                            ),
                            strokeColor=None,
                        )
                    )
                else:
                    points.extend([x, y(v)])
            if points:
                d.add(PolyLine(points, strokeColor=colors.HexColor("#173f5b"), strokeWidth=1.6))
        d.add(String(30, 3, series[0]["date"], fontName="ReaderCJK", fontSize=8))
        d.add(String(430, 3, series[-1]["date"], fontName="ReaderCJK", fontSize=8))
        story += [
            KeepTogether([
                p("法人與價格的同窗發現", "h3"),
                p("收盤、成交量與法人淨額共用交易日軸。分組結果見「跨面向研究與補查」。"
                  if any(item["method"] == "aligned_flow_20" for item in
                         data.get("workflow_review", {}).get("calculations", {}).get("cross_evidence_result", []))
                  else flow["summary"]),
                d,
            ]) if flow else d,
            p(ed.get("market_interpretation", "淨額和價格是描述性共現，不識別動機或因果。")),
            table(
                ["收盤區間", "日數", "外資淨額張", "投信淨額張"],
                [
                    [
                        r["label"] + " " + number(r["low"]) + "–" + number(r["high"]),
                        r["days"],
                        number(r["foreign"], 1000),
                        number(r["investment_trust"], 1000),
                    ]
                    for r in market["price_bins"]
                ],
            ),
            p(
                (
                    f"投信淨買{market['trust_buy_days']}日中，{market['trust_buy_down_days']}日股價下跌。價格分組不是法人買進成本；缺值不補零。"
                    if market["trust_buy_days"] is not None
                    else "投信分類不足，買超日與下跌日統計不可用；缺值不補零。"
                ),
                "small",
            ),
        ]
    for role, label in [("technical", "技術面分析師"), ("institutional", "籌碼面分析師")]:
        story.append(p(label, "h3"))
        story.extend(p(x) for x in analyst_paragraphs(notes[role]))
        story.append(p("改判條件：" + "；".join(notes[role]["conditions"]), "small"))
    story += [p("會改變EPS或結論的分歧", "h2")]
    if len(conclusions) > 3:
        story.append(p("裁決詳述", "h3"))
        story.extend(p(part) for part in conclusions[3:])
    judgment = data.get("debate_review", {}).get("judgment", {})
    if ruling_rows(data):
        story += [p(judgment.get("stronger_case", "")), p(judgment.get("evidence_that_changed_judgment", "")), p("改判條件：" + "；".join(judgment.get("revision_conditions", [])), "small")]
        material = debate_rows(data, material_only=True)
        if material:
            story.append(p("逐筆交叉反駁與原始紀錄收錄於 HTML 稽核版及證據包。", "small"))
    for row in ed.get("debate_focus", []):
        story += [
            p(row["issue"], "h3"),
            p(row["positions"]),
            p(row["impact"]),
            p("驗證條件：" + row["test"], "small"),
        ]
    story += [
        p("資料缺口與來源", "h2"),
        p(ed.get("material_unknowns", "未核實產品毛利與倍數保持未知，不用缺資料直接看空。")),
    ]
    for key, source in ed.get("sources", {}).items():
        story.append(
            p(
                key
                + "｜"
                + source["stock_id"]
                + "｜PDF實體頁"
                + ",".join(map(str, source["pages"]))
                + "｜公布"
                + str(source.get("published_at") or "未知")
                + "｜捕獲"
                + source["captured_at"]
                + "｜"
                + source["source_url"],
                "small",
            )
        )
    if (root / "company-package.json").exists():
        company = json.loads((root / "company-package.json").read_text("utf8"))
        story.append(p("逐筆數值與引句見證據包 company-evidence.html，對應原件在 company-originals。", "small"))
        source_index_path = root / "company-source-index.json"
        source_docs = json.loads(source_index_path.read_text("utf8")) if source_index_path.exists() else {}
        for key, source in source_docs.items():
            pages = sorted({str(f["page"]) for f in company["facts"] if f["source_id"] == key})
            if pages:
                citation = key + "｜實體頁 " + ",".join(pages) + "｜公布 " + str(source.get("published_at") or "未知")[:10] + "｜SHA256 " + source["sha256"]
                story.append(Paragraph(html.escape(citation) + ' · <link href="' + html.escape(source["source_url"], quote=True) + '">官方原件</link>', styles["small"]))
    story += [
        p(
            "分享時請解壓完整evidence.zip，再開report.html；原件、圖表資料及完整辯論在包內，系統稽核見audit.html。",
            "small",
        )
    ]

    def footer(canvas, doc):
        canvas.setFont("ReaderCJK", 8)
        canvas.setFillColor(colors.HexColor("#617b90"))
        canvas.drawString(16 * mm, 10 * mm, data["stock_id"] + "｜單次研究閱讀版｜非正式預測")
        canvas.drawRightString(196 * mm, 10 * mm, str(doc.page))

    doc = SimpleDocTemplate(
        str(root / "report.pdf"),
        pagesize=(210 * mm, 297 * mm),
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=16 * mm,
        bottomMargin=17 * mm,
        title=data["stock_id"] + " 研究閱讀版",
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
