"""Shared readable sections for HTML and PDF editions."""


def sections(plan):
    if not plan.get("context"):
        return []
    ctx, audit = plan["context"], plan["completeness"]
    forward, capital = plan["forward"], plan["capital"]
    labels = {
        "latest_research": "最新資料研究",
        "historical_replay": "歷史時點復盤",
        "retrospective_diagnostic": "現今回補版本的回顧診斷",
        "incomplete": "仍有來源覆蓋／更正版本待核對",
        "complete_within_declared_search": "已核對所聲明搜尋範圍",
        "unavailable_recognition": "EPS認列時點未核實",
        "unavailable_reviewed_capital_model_required": "缺完整期初股數與季度稀釋橋",
        "partial_four_quarter_bridges_missing_verified_drivers": "四季模型已列，必要驅動缺證處不計EPS",
        "conditional_four_quarter_models": "四季條件模型，非校準預測",
        "unavailable_missing_drivers": "營運量價／成本拆分不足，增量EPS不可用",
        "foundry": "晶圓代工",
        "thermal": "散熱與液冷",
        "odm": "伺服器ODM",
    }

    def label(value):
        return labels.get(value, value)

    rows = [
        (
            "研究模式與時間邊界",
            [
                ("模式／截止時刻", label(ctx["mode"]) + " / " + ctx["cutoff_at"]),
                (
                    "公告覆蓋檢查",
                    label(audit["status"]) + "；未覆蓋類別：" + "、".join(audit["coverage_gaps"]),
                ),
                (
                    "已公布獲利季度",
                    "、".join(audit["announced_earnings_periods"]) or "尚未核實完整清單",
                ),
                (
                    "重大公告缺件",
                    (
                        str(len(audit["missing_material_events"]))
                        + "筆待核對；逐筆差集見JSON／證據包"
                    )
                    if audit["missing_material_events"]
                    else "已列入清單者無缺件；不代表全站無遺漏",
                ),
                ("績效資格", "本次為當下修訂；已公布季度的機械試算不計事前績效"),
            ],
        )
    ]
    market = plan.get("latest_market_observation", {})
    if market.get("latest"):
        rows.insert(
            0,
            (
                "最新行情另列核對",
                [
                    (
                        "當日收盤／原流程行情日",
                        str(market["latest"]["values"]["close"])
                        + " 元（"
                        + market["latest"]["data_date"]
                        + "）；原次日資料流程截至 "
                        + str(market["original_normalized_last_date"]),
                    ),
                    (
                        "可用時點與資格",
                        market["captured_at"]
                        + " 起作公開捕獲觀測；不是歷史初版證明、沒有補發交易訊號",
                    ),
                ],
            ),
        )
    for actual in plan.get("reported_actuals", []):
        v = actual["values"]
        names = {
            "issued_shares": "已發行股數（億股）",
            "revenue_twd": "營收（億元）",
            "parent_profit_twd": "歸母淨利（億元）",
            "reported_diluted_eps_twd": "官方稀釋EPS（元）",
            "cfo_twd": "直接單季CFO（億元）",
            "capex_ppe_twd": "購置PPE（億元）",
        }
        rows.insert(
            1,
            (
                actual["period"] + " 已公布實績（不是預測）",
                [
                    (
                        names.get(k, k),
                        f"{value / 1e8:.5f}"
                        if (k.endswith("_twd") and k != "reported_diluted_eps_twd")
                        or k == "issued_shares"
                        else f"{value:.2f}",
                    )
                    for k, value in v.items()
                ],
            ),
        )
    cap = [
        (
            e["id"],
            {
                "authorization": "董事會授權",
                "approval": "條件／核准",
                "issue": "實發及認列核對",
                "cancellation": "註銷",
                "registration": "變更登記",
            }.get(e["kind"], e["kind"])
            + "；公布 "
            + str(e.get("published_at"))
            + "；生效／認列 "
            + str(e.get("recognition_date") or "未核實，不計季度加權")
            + "；股數 "
            + str(e.get("shares")),
        )
        for e in capital.get("timeline", [])
    ]
    cap += [
        ("股數模型", label(capital["status"])),
        (
            "基本加權／稀釋加權股數",
            str(capital.get("basic_weighted_shares") or "不可用")
            + " / "
            + str(capital.get("diluted_weighted_shares") or "不可用"),
        ),
        (
            "限制",
            "掛牌不等於EPS認列起日；已發行、基本加權與潛在稀釋股分開，不能在既有稀釋分母上再扣一次",
        ),
    ]
    rows.append(("股本重大事件時間軸", cap))
    op = plan["operating_model"]
    rows.append(
        (
            "公司專屬營運傳導",
            [
                ("模型", label(str(op.get("profile"))) + " / " + label(op["status"])),
                (
                    "尚缺營運變數",
                    (
                        {
                            "foundry": "各製程ASP及稼動、封裝量價與產能、海外增量成本及折舊切分",
                            "thermal": "伺服器出貨與供應份額、液冷組合與ASP、材料成本與良率、良品產能",
                            "odm": "AI及其他出貨／ASP／單位成本、付款條件、營運資金與增量融資成本",
                        }.get(op.get("profile"), "、".join(op.get("missing", [])))
                        if op.get("missing")
                        else "已給明示輸入，仍是條件方程而非因果驗證"
                    ),
                ),
                (
                    "模型差異",
                    "代工：產能×稼動×製程價格＋封裝；散熱：伺服器量×市占×液冷組合與良率／產能；ODM：出貨×單價、單位成本與營運資金及融資",
                ),
            ],
        )
    )
    frows = [
        ("四季期間", "、".join(forward["target_quarters"])),
        ("模型資格", label(forward["status"])),
    ]
    frows.append(
        (
            "股數資格",
            "未來各季均為明示假設；股本／潛在稀釋尚未銜接者不作已驗證估值分母。四季EPS和不等於正式年度稀釋EPS。",
        )
    )
    for name, model in forward["scenarios"].items():
        frows.append(
            (
                name + " 四季EPS和",
                (
                    f"{model['eps_sum_twd']:.3f}"
                    if model["eps_sum_twd"] is not None
                    else "不可用：季度必要假設未齊"
                )
                + "；每季獨立損益及股數；非單季乘四，非會計年度EPS",
            )
        )
        for q in model["quarters"]:
            c = q["calculation"]

            def money(value):
                return f"{value / 1e8:.2f}億" if value is not None else "未知"

            frows.append(
                (
                    name + " " + q["period"],
                    f"營收 {money(c['revenue_twd'])} → 毛利 {money(c['gross_profit_twd'])} → 營益 {money(c['operating_profit_twd'])} → 歸母 {money(c['parent_profit_twd'])} → EPS "
                    + (
                        f"{c['eps_twd']:.3f}"
                        if c["eps_twd"] is not None
                        else "未知；不以現股商數替代"
                    ),
                )
            )
    frows.append(
        (
            "市場共識",
            "已核對可比共識"
            if plan["expectations"]["consensus_verified"]
            else "不可用；沒有把公司單季營收指引當作四季稀釋EPS共識",
        )
    )
    rows.append(("未來四季與估值條件", frows))
    if plan["reverse"]:
        rows.append(
            (
                "反向估值",
                [
                    (
                        f"假設PE {r['assumed_pe']:.2f}",
                        f"所需四季EPS和 {r['required_eps_sum_twd']:.3f}；營收相對基準需 {r['required_revenue_growth_vs_base_pct']}%；共同毛利率需變動 {r['required_common_gross_margin_change_pp']} 個百分點；其餘不變",
                    )
                    for r in plan["reverse"]
                ],
            )
        )
    index = audit.get("official_index_reconciliation", {})
    state_labels = {
        "missing_document": "已發現但原件／更正銜接未驗收",
        "source_uncovered": "來源／窗口未覆蓋",
        "index_exhaustion_unverified": "官方回應無可核對總筆數，未證明已掃盡",
        "verified_no_updates": "已驗證指定窗口無更新",
        "reviewed_events": "窗口內事件均已核對",
    }
    names = {
        "capital": "股本",
        "earnings": "財報",
        "monthly_revenue": "月營收",
        "corrections": "更正",
    }
    rows.append(
        (
            "公告母集合差集驗收",
            [
                (
                    names.get(k, k),
                    state_labels.get(v["state"], v["state"])
                    + f"；索引觀測{v['observed_index_events']}筆，未處理{len(v['missing_event_ids'])}筆",
                )
                for k, v in index.get("categories", {}).items()
            ]
            + [
                (
                    "正式完整研究／估值",
                    "禁止：來源或模型尚未全通過"
                    if not plan.get("formal_valuation_allowed")
                    else "通過所聲明範圍，非投資績效證明",
                )
            ],
        )
    )
    disclosure = plan.get("financial_output_disclosure", {})
    drows = []
    for item in disclosure.get("rows", []):
        if item["scenario"] != "base":
            continue
        quotient = item["parent_profit_per_current_issued_share_twd"]
        drows.append(
            (
                item["period"],
                (
                    f"每股歸母商數 {quotient:.2f}元；非正式稀釋EPS。"
                    if quotient is not None
                    else "歸母橋不足，商數不可用。"
                )
                + "待補："
                + "、".join(x["driver"] for x in item["blockers"]),
            )
        )
    if drows:
        rows.append(
            (
                "前瞻輸出分級與股數敏感度",
                drows
                + [
                    (
                        "分母尺度",
                        "現股95%／100%／105%僅為假設軸，不是未來股數上下界；各值見完整計算。",
                    )
                ],
            )
        )
    illustration = plan.get("illustrative_profit_disclosure", {})
    irows = []
    for item in illustration.get("rows", []):
        if item["scenario"] != "base":
            continue
        c = item["calculation"]
        quotient = item["parent_profit_per_current_issued_share_twd"]
        irows.append(
            (
                item["period"],
                f"若明示歷史比率持平，歸母 {c['parent_profit_twd'] / 1e8:.2f}億；"
                + (f"現股商數{quotient:.2f}元" if quotient is not None else "商數分母未核對")
                + "；非預期值或稀釋EPS",
            )
        )
    total = illustration.get("four_quarter_reference_totals", {}).get("base", {})
    q = total.get("four_quarter_parent_profit_per_current_issued_share_twd")
    if q is not None:
        multiple = total.get("conditional_market_cap_to_parent_profit_multiple")
        irows.append(
            (
                "四季條件尺度",
                f"四季歸母合計／目前已發行股數 {q:.2f}元"
                + (
                    f"；參考股價日期{total['price_reference']['date']}，市值／條件歸母{multiple:.2f}倍"
                    if multiple is not None
                    else ""
                )
                + "。不是NTM稀釋EPS、Forward PE或合理價。",
            )
        )
    if irows:
        rows.append(
            (
                "歷史比率持平的條件損益",
                irows + [("用途", "完整損益與來源假設保留；不宣稱產業因果、概率或NTM稀釋EPS。")],
            )
        )
    ranked_rows = []
    for item in plan.get("illustrative_sensitivity", {}).get("base", []):
        ranked = item.get("ranking", [])
        if ranked:
            ranked_rows.append(
                (
                    item["period"],
                    "；".join(
                        f"{r['driver']}：歸母端點跨度{r['parent_profit_span_twd'] / 1e8:.2f}億"
                        for r in ranked[:3]
                    ),
                )
            )
    if ranked_rows:
        rows.append(
            (
                "條件敏感度順位（非機率）",
                ranked_rows
                + [
                    (
                        "解讀",
                        "依明示擾動寬度比較歸母變化，不是獨立變異數貢獻。匯率50%暴露是試算假設，不是已核對避險後暴露。",
                    )
                ],
            )
        )
    registry = plan.get("parameter_register", {})
    rows.append(
        (
            "產業參數與敏感度驗收",
            [
                (
                    "參數可用性",
                    f"待補{registry.get('missing_count', 0)}項；逐項來源、頻率、取得時間、可觀測性與誤差見參數表，未測量的誤差不填零。",
                ),
                (
                    "敏感度意義",
                    "一次變一項及明示聯合情境；不假設營收、毛利率、匯率等相互獨立。順位取決於指定擾動寬度，不是經驗風險機率。",
                ),
            ],
        )
    )
    return rows


def render(plan, table):
    import html

    if not plan.get("context"):
        return '<p role="alert">尚未提供研究模式、精確截止與重大公告覆蓋：不得標為即時完整研究或嚴格歷史事前績效。</p>'
    result = "".join(
        "<section><h2>"
        + html.escape(title)
        + "</h2>"
        + table(["檢查", "結果及限制"], rows)
        + "</section>"
        for title, rows in sections(plan)
    )
    disclosure = plan.get("illustrative_profit_disclosure", {})
    detail = []

    def amount(value):
        return f"{value / 1e8:.2f}億" if value is not None else "不可用"

    for item in disclosure.get("rows", []):
        c = item["calculation"]
        tax = item["inputs"]["tax_rate"]["value"] if item.get("inputs") else c.get("tax_rate")
        quotient = item["parent_profit_per_current_issued_share_twd"]
        detail.append(
            [
                item["scenario"] + " " + item["period"],
                "／".join(
                    amount(c.get(k))
                    for k in ("revenue_twd", "gross_profit_twd", "operating_profit_twd")
                ),
                amount(c.get("pretax_profit_twd"))
                + "／"
                + (f"{tax * 100:.2f}%" if tax is not None else "未知")
                + "／"
                + amount(c.get("parent_profit_twd")),
                f"{quotient:.3f}元；非稀釋EPS" if quotient is not None else "現股分母未核對",
                "、".join(x["driver"] for x in item["blockers"]) or "全部為明示條件，非官方實績",
            ]
        )
    if detail:
        result += (
            "<section><h2>四季三情境完整條件損益</h2><p>歷史比率持平的可重算例示；不是概率預測。各項來源、數值與假設原文見extension-input.json與計算結果。</p>"
            + table(
                [
                    "情境季度",
                    "營收／毛利／營益",
                    "稅前／稅率／歸母",
                    "每股歸母商數",
                    "EPS阻塞或資格",
                ],
                detail,
            )
            + "</section>"
        )
    return result
