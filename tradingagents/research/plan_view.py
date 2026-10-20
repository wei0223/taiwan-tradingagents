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
                    "、".join(audit["missing_material_events"])
                    or "已列入清單者無缺件；不代表全站無遺漏",
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
    return rows


def render(plan, table):
    import html

    if not plan.get("context"):
        return '<p role="alert">尚未提供研究模式、精確截止與重大公告覆蓋：不得標為即時完整研究或嚴格歷史事前績效。</p>'
    return "".join(
        "<section><h2>"
        + html.escape(title)
        + "</h2>"
        + table(["檢查", "結果及限制"], rows)
        + "</section>"
        for title, rows in sections(plan)
    )
