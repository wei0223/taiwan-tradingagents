"""Optional compact PDF edition from the same archived JSON, never browser capture."""

import html
import json
import re
from pathlib import Path


def render_pdf(directory, font_path):
    from reportlab.graphics.shapes import Drawing, Line, PolyLine, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        CondPageBreak,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    root = Path(directory)
    if not Path(font_path).is_file():
        raise ValueError("An installed CJK TrueType font is required")
    pdfmetrics.registerFont(TTFont("ResearchCJK", str(font_path), subfontIndex=0))
    styles = {
        "body": ParagraphStyle(
            "body", fontName="ResearchCJK", fontSize=10.5, leading=16, spaceAfter=3, wordWrap="CJK"
        ),
        "small": ParagraphStyle(
            "small",
            fontName="ResearchCJK",
            fontSize=9.2,
            leading=13.5,
            spaceAfter=4,
            wordWrap="CJK",
        ),
        "title": ParagraphStyle(
            "title", fontName="ResearchCJK", fontSize=21, leading=28, spaceAfter=12
        ),
        "h2": ParagraphStyle(
            "h2", fontName="ResearchCJK", fontSize=14, leading=20, spaceAfter=8, keepWithNext=True
        ),
        "h3": ParagraphStyle(
            "h3",
            fontName="ResearchCJK",
            fontSize=10.5,
            leading=15,
            spaceBefore=6,
            spaceAfter=5,
            keepWithNext=True,
        ),
    }

    citation_urls = {}

    def p(text, style="body"):
        escaped = html.escape(str(text)).replace("\n", "<br/>")
        escaped = re.sub(
            r"\[([A-Za-z][A-Za-z0-9_-]*)\]",
            lambda m: (
                '<link color="#176e82" href="'
                + html.escape(citation_urls[m[1]], quote=True)
                + '">['
                + m[1]
                + "]</link>"
                if m[1] in citation_urls
                else m[0]
            ),
            escaped,
        )
        return Paragraph(escaped, styles[style])

    def table(headers, rows, widths):
        data = [[p(c, "small") for c in row] for row in [headers, *rows]]
        result = Table(data, colWidths=widths, repeatRows=1, hAlign=TA_LEFT)
        result.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e5eff4")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#c7d5df")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        return result

    def chart(series, title, unit, height=145):
        d = Drawing(490, height)
        if len(series) < 2:
            return p("圖表不可用：觀測不足。")
        lo, hi = min(v for _, v in series), max(v for _, v in series)
        span = hi - lo or 1
        points = []
        for i, (_, value) in enumerate(series):
            points.extend(
                [30 + i / (len(series) - 1) * 435, 28 + (value - lo) / span * (height - 60)]
            )
        d.add(Line(30, 25, 470, 25, strokeColor=colors.HexColor("#a4b5c2")))
        d.add(PolyLine(points, strokeColor=colors.HexColor("#176e82"), strokeWidth=2))
        for x, y, text, size in [
            (30, height - 15, title, 10),
            (30, 8, series[0][0], 8),
            (400, 8, series[-1][0], 8),
            (30, height - 28, f"{lo:,.2f} - {hi:,.2f} {unit}", 8),
        ]:
            d.add(String(x, y, str(text), fontName="ResearchCJK", fontSize=size))
        return d

    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    data, notes, snap = read("research.json"), read("analysts.json"), read("snapshot.json")
    extra, debate = data["extension"], data["debate_review"]
    stock = data["stock_id"]
    citation_urls.update(
        {
            key: doc["source_url"]
            for key, doc in read("review-input.json").get("documents", {}).items()
        }
    )
    for alias, key in (("F01", "q1"), ("F02", "q2")):
        if key in citation_urls:
            citation_urls[alias] = citation_urls[key]
    current = data["review"]["axes"]["accounting_earnings"].get("current", {})
    target_period = max((r["period"] for r in extra["financial_cases"]), default="")
    q3 = [r for r in extra["financial_cases"] if r["period"] == target_period]
    values = [r["calculation"]["eps_twd"] for r in q3]
    plan = extra.get("research_plan", {})
    if plan.get("context"):
        target_period = plan["forward"]["target_quarters"][0]
        q3 = [
            s["quarters"][0]
            for s in plan["forward"]["scenarios"].values()
            if s["quarters"][0]["calculation"]["eps_twd"] is not None
        ]
        values = [r["calculation"]["eps_twd"] for r in q3]
    hist = extra["valuation"].get("reported_history", {})
    story = [
        p(stock + " 研究摘要儀表板", "title"),
        p(
            "實際產生 "
            + data["generated_at"]
            + "｜資料分析日 "
            + data["as_of"]
            + "｜行情 "
            + str(data["dynamics"].get("latest_session")),
            "small",
        ),
        p(
            "本版重審所列資料截止日的既有證據，不代表產生日期的即時資料。原Agent提交保持原日期，本版新增計算不宣稱已重新辯論。已公布實績不計預測命中。完整原件、模型與辯論另附證據包。"
            if extra.get("research_plan", {}).get("context")
            else "本版為既有資料的模型與論述升級，非即時更新或事前預測。完整證據包含所有引用資料、計算、獨立Agent原稿與檢查；PDF不是完整重現資料的替代品。",
            "small",
        ),
    ]
    story += [
        table(
            ["研究面向", "目前可證明的內容"],
            [
                *[
                    [
                        a["period"] + " 官方已公布",
                        f"稀釋EPS {a['values']['reported_diluted_eps_twd']:.2f}；已在本版模型前公布，非預測命中",
                    ]
                    for a in plan.get("reported_actuals", [])
                    if "reported_diluted_eps_twd" in a["values"]
                ],
                [
                    "稀釋附註已核對季度",
                    current.get("period_end", "不可用")
                    + "；稀釋EPS "
                    + (f"{current['eps_twd']:.3f}" if current else "不可用"),
                ],
                [
                    target_period + " 條件EPS跨度",
                    f"{min(values):.2f} - {max(values):.2f} 元；不是預測區間或機率"
                    if values
                    else "不可用",
                ],
                [
                    "歷史來源報列PE",
                    f"中位數 {hist['median']:.2f}；分母未驗證，不是歷史Forward PE"
                    if hist.get("median")
                    else "不可用",
                ],
                ["好公司／好成長／好價格", "歷史營運可核對；未來驅動仍有假設；估值吸引力尚未證立"],
                ["短期交易", "觀察；R僅情境測量；沒有成本後統計優勢證明"],
            ],
            [43 * mm, 131 * mm],
        ),
        Spacer(1, 6 * mm),
    ]
    story += [
        chart(
            [(r["period"], r["revenue_twd"] / 1e8) for r in snap.get("monthly_revenue", [])[-18:]],
            "已公布月營收趨勢",
            "億元",
        )
    ]
    if debate["independent_agents_executed"]:
        ruling = debate["judgment"]
        story += [
            p("核心分歧與裁決", "h2"),
            p(ruling["stronger_case"]),
            p(ruling["evidence_that_changed_judgment"]),
        ]
    story += [
        p(
            "尚未通過：產品量價到利潤的因果驗證、未來加權股數、完整回款橋接、可驗證前瞻估值分母、真正未接觸的前瞻樣本外績效。",
            "small",
        )
    ]
    val = extra["valuation"]
    if val.get("reverse"):
        story += [
            p("反向估值敏感度", "h3"),
            table(
                ["假設倍數", "現價倒推所需EPS", "成長分母"],
                [
                    [
                        f"{r['assumed_pe']:.2f}",
                        f"{r['required_eps_twd']:.2f}",
                        (
                            f"{r['required_growth_vs_reference_pct']:.2f}%／{r.get('reference_basis')}"
                            if r["required_growth_vs_reference_pct"] is not None
                            else "不足，暫不推成長率"
                        ),
                    ]
                    for r in val["reverse"]
                ],
                [50 * mm, 60 * mm, 64 * mm],
            ),
            p("倍數軸來自來源報列歷史分位數，只作敏感度。不是已觀測市場共識或合理價。", "small"),
        ]
    pe_story = []
    receipt = extra["valuation"].get("sample_receipt", {})
    version = extra["valuation"].get("version_comparison", {})
    if receipt:
        pe_story += [
            p("PE口徑與版本稽核", "h3"),
            p(
                f"固定樣本{receipt['first_date']}至{receipt['last_date']}，有效{receipt['count']}筆。分母及價格追溯調整仍未核對，只作來源報列PE描述；不當作已驗證TTM或Forward PE。",
                "small",
            ),
        ]
        change = version.get("attribution")
        if change:
            pe_story += [
                p(
                    f"相對前版中位數改變{change['total_change']:.3f}倍：刪除日期{change['removed_dates_effect']:.3f}、共同日期修訂{change['common_date_revision_effect']:.3f}、新增日期{change['added_dates_effect']:.3f}。這是固定順序的數學分解，非經濟因果。",
                    "small",
                )
            ]
    from .plan_view import sections as plan_sections

    plan_rows = plan_sections(extra.get("research_plan", {}))
    if plan_rows:
        story += [PageBreak(), p("時間、股本與未來四季驗收", "h2"), *pe_story]
        for heading, rows in plan_rows:
            if heading == "最新行情另列核對":
                rows = rows[:1]
            elif heading == "研究模式與時間邊界":
                rows = rows[:1] + rows[1:2] + rows[3:4]
            elif heading == "公司專屬營運傳導":
                rows = rows[:2]
            elif heading == "股本重大事件時間軸":
                rows = [r for r in rows if r[0] != "限制"]
            elif "已公布實績" in heading:
                rows = [("已核對原件", "；".join(k + " " + v for k, v in rows))]
            if heading == "未來四季與估值條件":
                forward = plan["forward"]

                def num(value, scale=1e8):
                    return f"{value / scale:.2f}" if value is not None else "待證"

                compact = []
                for i, period in enumerate(forward["target_quarters"]):
                    models = forward["scenarios"]
                    if not models:
                        continue
                    base = models["base"]["quarters"][i]["calculation"]
                    compact.append(
                        [
                            period,
                            " / ".join(
                                num(models[s]["quarters"][i]["calculation"]["revenue_twd"])
                                for s in ("bear", "base", "bull")
                            ),
                            num(base["operating_profit_twd"]),
                            num(base["parent_profit_twd"]),
                            num(base["eps_twd"], 1),
                        ]
                    )
                story += [
                    p(heading, "h3"),
                    table(
                        [
                            "季度",
                            "營收：保／基／樂（億）",
                            "基準營益億",
                            "基準歸母億",
                            "基準稀釋EPS",
                        ],
                        compact,
                        [26 * mm, 63 * mm, 28 * mm, 28 * mm, 29 * mm],
                    ),
                    p(
                        "四季逐項完整損益、三情境及假設見HTML與extension.json。待證欄位不計四季EPS和或Forward PE；營收方法範圍不代表信心區間。",
                        "small",
                    ),
                ]
            else:
                story += [
                    p(heading, "h3"),
                    table(["檢查", "結果及限制"], rows, [43 * mm, 131 * mm]),
                ]
    story += [CondPageBreak(65 * mm), p("基本面分析師｜從營運到每股獲利", "h2")]
    n = notes["fundamental"]
    for field, heading in (
        ("primary_claim", "主張"),
        ("quantified_evidence", "量化證據"),
        ("strongest_counterevidence", "最強反證"),
    ):
        story += [p(heading + "：" + n.get(field, n["position"]))]
    for para in n["paragraphs"]:
        if para.startswith(("未來四季逐季列", "獨立多空先", "多空若主張", "新增「歷史比率持平」")):
            continue
        story.append(p(para))
    if q3:
        story += [
            p(
                "成本配置限制：目前只凍結總成本及費用率，折舊已含其中。沒有另加折舊不代表公司零折舊，亦未識別擴產固定成本；完整假設與稅、非控股、分子及股數見extension-input.json。",
                "small",
            ),
            p(target_period + " 完整傳導比較（條件試算）", "h3"),
            table(
                ["模型", "營收億", "毛利億", "營益億", "歸母億", "稀釋EPS"],
                [
                    [
                        r["name"],
                        *[
                            f"{r['calculation'][k] / 1e8:.2f}"
                            for k in (
                                "revenue_twd",
                                "gross_profit_twd",
                                "operating_profit_twd",
                                "parent_profit_twd",
                            )
                        ],
                        f"{r['calculation']['eps_twd']:.3f}",
                    ]
                    for r in q3
                ],
                [49 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm, 25 * mm],
            ),
        ]
    detail = extra["cash_quality"]["details"]
    if detail["status"] == "available":
        v = detail["values"]
        story += [
            p(
                "現金品質（"
                + detail["period_start"]
                + "至"
                + detail["period_end"]
                + "累計，非單季）",
                "h3",
            ),
            table(
                ["項目", "億元", "項目", "億元"],
                [
                    [
                        "CFO",
                        f"{v['cfo_twd'] / 1e8:.2f}",
                        "PPE現金支出",
                        f"{v['capex_ppe_twd'] / 1e8:.2f}",
                    ],
                    [
                        "應收現金影響",
                        f"{v['ar_cash_effect_twd'] / 1e8:.2f}",
                        "存貨現金影響",
                        f"{v['inventory_cash_effect_twd'] / 1e8:.2f}",
                    ],
                    [
                        "應付現金影響",
                        f"{v['ap_cash_effect_twd'] / 1e8:.2f}",
                        "籌資淨流量",
                        f"{v['financing_cashflow_twd'] / 1e8:.2f}",
                    ],
                    [
                        "CFO減PPE",
                        f"{detail['fcf_ppe_only_twd'] / 1e8:.2f}",
                        "再扣無形支出",
                        f"{detail['fcf_ppe_and_intangibles_twd'] / 1e8:.2f}",
                    ],
                ],
                [49 * mm, 38 * mm, 49 * mm, 38 * mm],
            ),
        ]
        days = detail["days"]
        if days["status"] == "available_proxy":
            story += [
                p(
                    f"兩點平均代理：DSO {days['dso']:.1f}天、DIO {days['dio']:.1f}天、DPO {days['dpo_cogs_proxy']:.1f}天、CCC {days['ccc_cogs_proxy']:.1f}天。DPO用COGS代替賒購，不等於合約付款條件。",
                    "small",
                )
            ]
    story += [
        p("改判條件：" + "；".join(n["conditions"])),
        p(n.get("confidence", "未校準質性信心，無統計區間。"), "small"),
    ]
    story += [CondPageBreak(65 * mm), p("技術面與籌碼面｜獨立交易尺度", "h2")]
    prices = snap.get("extensions", {}).get("TaiwanStockPriceAdj", {}).get("records", [])[-90:]
    story += [
        chart(
            [(r["data_date"], r["values"]["close"]) for r in prices],
            "供應商還原收盤價（非含息總報酬）",
            "元",
            height=110,
        )
    ]
    for role, label in (("technical", "技術面分析師"), ("institutional", "籌碼面分析師")):
        n = notes[role]
        story += [p(label, "h3"), p(n["position"])]
        if role == "institutional":
            story.append(p("量化證據：" + n.get("quantified_evidence", "見完整計算")))
        for i, para in enumerate(n["paragraphs"]):
            if role == "institutional" and i == 0 and "此為" in para:
                para = "此為" + para.split("此為", 1)[1]
            story.append(p(para))
        story += [
            p("最強反證：" + n.get("strongest_counterevidence", n["disagreement"])),
            p("改判條件：" + "；".join(n["conditions"])),
            p(n.get("confidence", "未校準質性信心。"), "small"),
        ]
    replay = extra["backtest"]
    if replay.get("splits"):
        holdout = replay["splits"]["holdout"]
        story += [
            p("保留段成本後重播（回顧診斷，非獨立樣本外證明）", "h3"),
            table(
                ["期限", "事件數", "淨勝率", "淨報酬中位數", "最差單筆收盤回撤"],
                [
                    [
                        h,
                        r["count"],
                        f"{r['net_win_rate'] * 100:.1f}%"
                        if r.get("net_win_rate") is not None
                        else "樣本不足",
                        f"{r['return_distribution']['p50'] * 100:.2f}%"
                        if r["return_distribution"]["p50"] is not None
                        else "樣本不足",
                        f"{r['worst_trade_marked_close_drawdown'] * 100:.2f}%"
                        if r["count"]
                        else "不可用",
                    ]
                    for h, r in holdout.items()
                ],
                [22 * mm, 25 * mm, 35 * mm, 43 * mm, 49 * mm],
            ),
            p(
                "事件太少，不能推定勝率或統計優勢；期限相互重疊。成本是明示假設，不是現行券商費率驗證。",
                "small",
            ),
        ]
        individual = [
            [h, e["signal_date"], f"{e['net_return'] * 100:.2f}%"]
            for h, r in holdout.items()
            if r.get("aggregate_statistics_suppressed")
            for e in r["events"]
        ]
        if individual:
            story += [
                p("小樣本逐筆結果（同日不同期限相互關聯）", "h3"),
                table(
                    ["期限", "訊號日", "單筆成本後報酬"], individual, [35 * mm, 75 * mm, 64 * mm]
                ),
            ]
    story += [
        Spacer(1, 5 * mm),
        p("交叉質詢摘要與證據稽核", "h2"),
        p("正文保留每方一項關鍵反駁；完整逐項回覆與原始提交位元組見證據包及HTML附錄。", "small"),
    ]
    if debate["independent_agents_executed"]:
        for role, label in (("bull", "多方"), ("bear", "空方")):
            reply = debate["replies"][role]
            story += [
                p(label + "：" + reply["position_before"] + " → " + reply["position_after"], "h3")
            ]
            for r in reply["changes"][:1]:
                story += [p(r["claim_id"] + "／" + r["assessment"] + "：" + r["reason"], "small")]
        risk = debate["risk"]
        story += [p("獨立風險審查", "h3")]
        for finding in risk.get("findings", []):
            if isinstance(finding, dict) and finding.get("severity") in {
                "material_model_assumption",
                "omitted_expense_driver",
                "unmodeled_nonrecurring_mix",
                "cash_material",
                "cash_quality",
            }:
                story.append(p(finding["finding"], "small"))
        story += [
            p("主管裁決：" + debate["judgment"]["after"]),
            p(
                "未定事項："
                + (
                    "；".join(debate["judgment"]["unresolved"])
                    if isinstance(debate["judgment"]["unresolved"], list)
                    else str(debate["judgment"]["unresolved"])
                ),
                "small",
            ),
        ]
    story += [
        p("證據入口與重現", "h3"),
        p(
            "完整證據包收錄原件、行情與法人資料、全部假設與計算、原Agent提交及SHA256清單。解開ZIP後開啟report.html核對引用；只取得PDF仍無法重現全部研究。",
            "small",
        ),
    ]
    cited = {"q1", "q2", "Ntsmc_q3_financial"}
    cited |= {key for key in citation_urls if "F03" in key}
    story.append(
        p(
            "引用可點開官方來源；完整時點、版本與SHA256在證據包。下列僅列主要原件雜湊前綴。",
            "small",
        )
    )
    for key, doc in read("review-input.json").get("documents", {}).items():
        if key not in cited:
            continue
        story.append(
            p("[" + key + "] SHA256前16碼 " + doc["sha256"][:16] + "；完整雜湊見證據包", "small")
        )
    story += [
        p(
            "原封存預測及發布時間不變；新試算另存，不能回填事前績效。完整重現須保存證據包。",
            "small",
        )
    ]
    original = data.get("forecast_preservation", {})
    if original.get("original_issued_at"):
        story.append(
            p(
                "原預測發布時間 "
                + original["original_issued_at"]
                + "；原release副本保存在sealed-releases/，本版未修改原值。",
                "small",
            )
        )

    def footer(canvas, doc):
        canvas.setFont("ResearchCJK", 7.5)
        canvas.setFillColor(colors.HexColor("#536c7c"))
        canvas.drawString(18 * mm, 11 * mm, stock + "｜研究模型修訂｜各資料時點分列｜原封存不變")
        canvas.drawRightString(192 * mm, 11 * mm, str(doc.page))

    target = root / "report.pdf"
    SimpleDocTemplate(
        str(target),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=14 * mm,
        bottomMargin=16 * mm,
        title=stock + " 研究模型與證據報告",
    ).build(story, onFirstPage=footer, onLaterPages=footer)
    return target
