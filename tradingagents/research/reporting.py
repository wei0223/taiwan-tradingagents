"""Portable three-analyst research package with a short decision front and audit appendices."""

import base64
import copy
import hashlib
import html
import json
from datetime import UTC, datetime
from pathlib import Path

from .bundle import build_bundle, fingerprint
from .debate import write_json
from .dynamics import conditional_decision, reward_risk, rolling
from .quality import audit_html, industry_review, source_audit
from .scenarios import check_assumption, forecast


def table(headers, rows):
    return (
        '<div class="scroll"><table><tr>'
        + "".join("<th>" + html.escape(str(h)) + "</th>" for h in headers)
        + "</tr>"
        + "".join(
            "<tr>" + "".join("<td>" + html.escape(str(c)) + "</td>" for c in row) + "</tr>"
            for row in rows
        )
        + "</table></div>"
    )


def package(snapshot, output_dir, specification=None, sources=None, analyst_prose=None):
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
        "independent_agents_executed": False,
        "prediction_accuracy": {"status": "pending_future_actuals", "mae": None},
        "strategy_performance": {
            "status": "unavailable",
            "reason": "No sealed comparable prospective signals and resolved outcomes",
        },
        "llm_api_calls": False,
        "orders": False,
    }
    result["generated_at"] = datetime.now(UTC).isoformat()
    result["retrospective_model"] = bool(
        specification and specification.get("created_at", "")[:10] > as_of
    )
    summary = (
        "<h2>決策摘要</h2><p><strong>目前："
        + (
            "條件式研究候選"
            if decision["state"] == "research_candidate"
            else "觀察，不形成交易訊號"
        )
        + "</strong>。基本面估算、價格確認與法人視窗分開檢核，不因報告完成而產生買賣指令。</p>"
    )
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
        summary += "<h2>前瞻 EPS 與估值：暫停估算</h2><p>缺少經正式原件核對的季度口徑、母公司歸屬、加權股數或明示情境。缺口未補齊前，不由歷史 EPS 或新聞目標價反推。</p>"
    summary += "<h2>什麼會改變判斷？</h2>" + table(
        ["方向", "可檢核條件"],
        [
            ["提高支持", "正式資料支持模型的營收與利潤率、完整法人視窗改善，並出現價格確認。"],
            ["降低支持", "新公告推翻盈利假設、突破失敗或資料完整性降低；重新計算，不沿用舊訊號。"],
            ["維持觀察", "條件尚未滿足或互相衝突；不因低歷史倍數或單日買超強行轉多。"],
        ],
    )
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
    prose = analyst_prose or {}
    roles = [
        ("fundamental", "基本面分析師"),
        ("technical", "技術面分析師"),
        ("institutional", "籌碼面分析師"),
    ]
    for role, label in roles:
        body = "".join("<p>" + html.escape(p) + "</p>" for p in prose.get(role, []))
        if role == "fundamental":
            body += "<p>營收規模、利潤率與現金轉換分開判斷；未取得的產品組合與同業資料明列缺口。歷史成長不直接保證估值吸引力。</p>"
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
                            f"突破 {r['breakout_previous_window_high']}；單日假突破規則 {r['false_breakout_one_session_rule']}",
                        ]
                    )
                else:
                    rows.append(
                        [
                            w,
                            "；".join(
                                f"{n}: {g['window_net_shares'] if g['window_net_shares'] is not None else '不可用'} 股"
                                for n, g in r["institutions"].items()
                            ),
                            r["start"] + " 至 " + r["end"],
                        ]
                    )
            body += table(["視窗（交易日）", "程式計算", "觀測範圍／判定"], rows)
            body += "<p>技術型態為明示機械規則，並非已驗證勝率；ATR 使用真實波幅簡單平均。缺日不補零，法人淨額不代表全部籌碼或因果證明。</p>"
        details += "<details><summary>" + label + "完整分析</summary>" + body + "</details>"
    # Keep raw PDF bytes in JSON/archive, never render megabytes of base64 as prose.
    display = copy.deepcopy(result)
    for doc in (
        display.get("forecast", {})
        .get("assumptions", {})
        .get("basis_review", {})
        .get("documents", [])
    ):
        doc.pop("content_base64", None)
        doc["content_location"] = (
            "research.json contains original document bytes; sha256 retained here"
        )
    details += (
        "<details><summary>來源、計算與模型完整稽核</summary><pre>"
        + html.escape(json.dumps(display, ensure_ascii=False, indent=2))
        + "</pre></details>"
    )
    css = "body{font:16px/1.8 system-ui;background:#f1f4f7;color:#193047;margin:0}main{max-width:1050px;margin:24px auto;background:white;padding:40px}h1{border-top:6px solid #193047;padding-top:18px}h2{font-size:22px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{padding:10px;border-bottom:1px solid #dce4eb;text-align:left;vertical-align:top}th{background:#edf2f7}.scroll{overflow:auto}details{padding:18px 0;border-top:1px solid #ccd6df}summary{cursor:pointer;font-weight:bold}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}@media print{main{margin:0;padding:12px}.appendix{break-before:page}}"
    page = (
        '<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'
        + stock
        + " 研究升級版</title><style>"
        + css
        + "</style><main><h1>"
        + stock
        + " 三分析師研究報告</h1><p>分析資料日："
        + as_of
        + "｜資料版本 "
        + fingerprint(snapshot)[:12]
        + "｜程式模型與編輯論述，未執行新獨立 Agent</p>"
        + summary
        + '<div class="appendix">'
        + details
        + "</div></main></html>"
    )
    links = audit_html(page)
    if links["status"] != "passed":
        raise ValueError("Nonportable report")
    result["html_audit"] = links
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if model["status"] == "ready":
        documents = output / "documents"
        documents.mkdir(exist_ok=True)
        for doc in model["assumptions"]["basis_review"]["documents"]:
            content = base64.b64decode(doc["content_base64"], validate=True)
            extension = ".pdf" if content.startswith(b"%PDF") else ".bin"
            (documents / (doc["sha256"] + extension)).write_bytes(content)
    write_json(output / "research.json", result)
    (output / "report.html").write_text(page, encoding="utf-8", newline="\n")
    write_json(
        output / "manifest.json",
        {
            "stock_id": stock,
            "as_of": as_of,
            "bundle_sha256": fingerprint(bundle),
            "research_sha256": fingerprint(result),
            "html_sha256": hashlib.sha256(page.encode()).hexdigest(),
        },
    )
    return result
