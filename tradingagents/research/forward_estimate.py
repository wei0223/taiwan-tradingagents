"""Explicit, source-anchored four-quarter estimates alongside incomplete audit scopes.

Historical version completeness, conditional forecast availability and fair value
are separate questions. An inferred future share count is not an observed EPS note.
"""

import base64
import hashlib
import html
import math

from .bundle import fingerprint
from .financial_model import waterfall
from .forward_model import reverse
from .knowledge import source_eligibility
from .scenarios import finite, next_quarters


def ttm(rows, documents, context, stock):
    """Only directly disclosed quarterly diluted EPS; never annual-minus-nine-month EPS."""
    from tradingagents.dataflows.taiwan.periods import superseded_documents

    superseded = superseded_documents(documents, stock, context["cutoff_at"])
    if len(rows) != 4 or any(r.get("source_id") in superseded for r in rows):
        return {
            "status": "unavailable",
            "eps_twd": None,
            "reason": "Four directly disclosed comparable quarterly diluted EPS originals required",
        }
    selected = sorted(rows, key=lambda r: r["period"])
    indices = [int(r["period"][:4]) * 4 + int(r["period"][-1]) for r in selected]
    if indices != list(range(indices[0], indices[0] + 4)):
        raise ValueError("Four distinct consecutive TTM quarters required")
    for row in selected:
        doc = documents.get(row.get("source_id"), {})
        if (
            row.get("basis") != "direct_reported_single_quarter_diluted_eps"
            or row.get("stock_id") != stock
            or not row.get("reviewed_by")
            or not row.get("locator")
            or row.get("currency") != "TWD"
            or row.get("initial_or_revised_vintage")
            not in {"captured_current", "initial", "revised"}
            or not doc
            or not source_eligibility(doc, context)["eligible"]
        ):
            raise ValueError(
                "TTM requires reviewed direct quarterly disclosure and eligible original"
            )
        finite(row["eps_twd"])
        if row["period"] > context["cutoff_at"][:4] + "-Q" + str(
            (int(context["cutoff_at"][5:7]) - 1) // 3 + 1
        ):
            raise ValueError("Future TTM quarter")
    return {
        "status": "available_reported_quarterly_sum",
        "quarters": selected,
        "eps_twd": sum(finite(r["eps_twd"]) for r in selected),
        "type": "sum_reported_quarterly_diluted_eps_not_statutory_annual_weighted_eps",
        "rounding_bound_twd": 0.02,
        "strict_historical_vintage_verified": False,
    }


def build(spec, plan, quality, documents, stock):
    if not spec:
        return {"status": "not_supplied"}
    if spec.get("stock_id") != stock or spec.get("currency") != "TWD":
        raise ValueError("Company-specific forward estimate currency/identity required")
    if not spec.get("methodology") or not spec.get("reviewed_by"):
        raise ValueError("Forward estimate needs method and named review")
    ctx = plan["context"]
    for key, doc in documents.items():
        raw = base64.b64decode(doc["content_base64"], validate=True)
        if doc.get("stock_id") != stock or hashlib.sha256(raw).hexdigest() != doc["sha256"]:
            raise ValueError("Estimate original company/hash mismatch: " + key)
    forward = plan["forward"]
    targets = next_quarters(ctx["cutoff_at"][:10])
    rows = quality["forward_parameter_register"]
    valid = (
        forward.get("target_quarters") == targets
        and len(rows) == 12
        and {(r["scenario"], r["period"]) for r in rows}
        == {(s, q) for s in ("bear", "base", "bull") for q in targets}
        and all(r["formal_eps_status"] == "PASS" for r in rows)
    )
    blockers = []
    for row in rows:
        for param in row["parameters"]:
            for sid in param["source"]:
                if sid not in documents or not source_eligibility(documents[sid], ctx)["eligible"]:
                    valid = False
                    blockers.append(sid + ":source_unavailable")
    for model in forward.get("scenarios", {}).values():
        for case in model["quarters"]:
            values = {k: item["value"] for k, item in case["inputs"].items()}
            if any(v is None for v in values.values()):
                valid = False
                continue
            recomputed = waterfall(values)
            for key, value in recomputed.items():
                if isinstance(value, (int, float)) and not math.isclose(
                    value, case["calculation"][key], rel_tol=1e-10, abs_tol=0.01
                ):
                    raise ValueError("Serialized earnings bridge does not recompute: " + key)
        quarter_eps = [c["calculation"].get("eps_twd") for c in model["quarters"]]
        if all(v is not None for v in quarter_eps) and (
            model.get("eps_sum_twd") is None
            or not math.isclose(sum(quarter_eps), model["eps_sum_twd"], rel_tol=1e-10, abs_tol=1e-8)
        ):
            raise ValueError("Four-quarter EPS sum does not reconcile")
    trailing = ttm(spec.get("ttm_quarters", []), documents, ctx, stock)
    price = quality["valuation"]["price_twd"]
    fresh = quality["latest_market_gate"]["status"] == "PASS"
    axes = [finite(x) for x in spec.get("pe_sensitivity", [])]
    if (
        not axes
        or any(x <= 0 for x in axes)
        or axes != sorted(set(axes))
        or not spec.get("pe_rationale")
    ):
        raise ValueError(
            "Explicit positive ordered company-specific PE sensitivity axis and rationale required"
        )
    scenarios = {}
    for name, model in forward.get("scenarios", {}).items():
        eps = model["eps_sum_twd"] if valid else None
        scenarios[name] = {
            "quarters": [{"period": c["period"], **c["calculation"]} for c in model["quarters"]],
            "four_quarter_eps_sum_twd": eps,
            "forward_pe": price / eps if fresh and eps and eps > 0 else None,
            "price_sensitivity_twd": [eps * x for x in axes] if fresh and eps and eps > 0 else [],
        }
    sensitivity = []
    if valid:
        for driver, change in [
            ("revenue_twd", 1.1),
            ("variable_cogs_ratio", -0.01),
            ("variable_opex_ratio", 0.005),
            ("tax_rate", 0.02),
            ("diluted_weighted_shares", 1.01),
        ]:
            total = 0
            for c in forward["scenarios"]["base"]["quarters"]:
                values = {k: x["value"] for k, x in c["inputs"].items()}
                if driver in {"revenue_twd", "diluted_weighted_shares"}:
                    values[driver] *= change
                else:
                    values[driver] += change
                total += waterfall(values)["eps_twd"]
            sensitivity.append(
                {
                    "driver": driver,
                    "change": change,
                    "eps_sum_twd": total,
                    "eps_delta_twd": total - forward["scenarios"]["base"]["eps_sum_twd"],
                    "scope": "all_four_quarters_same_shock_other_inputs_fixed_not_probability",
                }
            )
    result = {
        "status": "conditional_estimate_reviewed_assumptions" if valid else "BLOCKED",
        "stock_id": stock,
        "created_at": ctx["cutoff_at"],
        "methodology": spec["methodology"],
        "model_version": spec.get("model_version"),
        "reviewed_by": spec["reviewed_by"],
        "target_quarters": targets,
        "parameter_register": rows,
        "blockers": sorted(set(blockers)),
        "scenarios": scenarios,
        "ttm": trailing,
        "price_date": quality["valuation"]["price_date"],
        "price_twd": price,
        "price_basis": "unadjusted_TWD_per_common_share",
        "price_fresh": fresh,
        "ttm_pe": price / trailing["eps_twd"]
        if fresh and trailing.get("eps_twd") and trailing["eps_twd"] > 0
        else None,
        "pe_sensitivity": axes,
        "sensitivity": sensitivity,
        "share_bridge": spec.get("share_bridge", {}),
        "pe_rationale": spec["pe_rationale"],
        "reverse": [reverse(price, pe, forward["scenarios"]["base"]["quarters"]) for pe in axes]
        if valid and fresh
        else [],
        "scenario_implied_multiple_is_forward_pe": False,
        "full_fair_value": None,
        "undervaluation_rank": None,
        "statistically_calibrated": False,
        "probability_interval": None,
        "future_share_basis": "explicit_quarterly_weighted_diluted_assumptions_not_verified_future_actuals",
        "risks": spec.get("risks", [])
        + [
            "Official announcement coverage and historical version gaps remain disclosed separately",
            "An EPS estimate is not company guidance or verified market consensus",
            "Four quarterly EPS estimates summed are not statutory annual weighted diluted EPS",
            "PE axes are conditional sensitivity, not independently established fair exit multiples",
        ],
    }
    result["model_sha256"] = fingerprint(result)
    return result


def render(model, table):
    if model.get("status") == "not_supplied":
        return ""
    esc = html.escape
    out = (
        '<section id="forward-estimate"><h2>四季 EPS 預估與條件估值</h2><p>'
        + esc(model["methodology"])
        + "</p>"
    )
    out += (
        "<p>模型資格："
        + esc(model["status"])
        + "。未來股數、成本與稅率是明示假設，不是已知實績；沒有校準機率。行情 "
        + esc(str(model["price_date"]))
        + "，未還原收盤 "
        + f"{model['price_twd']:.2f} 元。</p>"
    )
    out += table(
        ["情境", "四季 EPS 和（元）", "Forward PE（條件）"],
        [
            [
                name,
                f"{s['four_quarter_eps_sum_twd']:.2f}"
                if s["four_quarter_eps_sum_twd"] is not None
                else "BLOCKED",
                f"{s['forward_pe']:.2f}" if s["forward_pe"] is not None else "不可用",
            ]
            for name, s in model["scenarios"].items()
        ],
    )
    out += table(
        ["季度／基準", "營收億", "毛利億", "營益億", "業外億", "歸母億", "稀釋股數百萬", "EPS元"],
        [
            [
                c["period"],
                *[
                    f"{c[k] / 1e8:.2f}" if c[k] is not None else "未知"
                    for k in ("revenue_twd", "gross_profit_twd", "operating_profit_twd")
                ],
                f"{c['pretax_profit_twd'] / 1e8 - c['operating_profit_twd'] / 1e8:.2f}"
                if c["pretax_profit_twd"] is not None
                else "未知",
                f"{c['parent_profit_twd'] / 1e8:.2f}"
                if c["parent_profit_twd"] is not None
                else "未知",
                f"{c['diluted_weighted_shares'] / 1e6:.3f}"
                if c["diluted_weighted_shares"] is not None
                else "未知",
                f"{c['eps_twd']:.2f}" if c["eps_twd"] is not None else "未知",
            ]
            for c in model["scenarios"].get("base", {}).get("quarters", [])
        ],
    )
    trailing = model["ttm"]
    assumptions = []
    for row in model["parameter_register"]:
        if row["scenario"] != "base":
            continue
        values = {p["parameter_name"]: p["value"] for p in row["parameters"]}
        needed = [
            "variable_cogs_ratio",
            "variable_opex_ratio",
            "tax_rate",
            "noncontrolling_profit_fraction",
        ]
        assumptions.append(
            [
                row["period"],
                *[
                    f"{((1 - values[k]) if k == 'variable_cogs_ratio' else values[k]) * 100:.2f}%"
                    if values.get(k) is not None
                    else "未知"
                    for k in needed
                ],
                "、".join(sorted({s for p in row["parameters"] for s in p["source"]})),
            ]
        )
    out += "<h3>基準參數（不代表最可能結果）</h3>" + table(
        ["季度", "毛利率", "費用率", "稅率", "非控股獲利比", "來源ID"], assumptions
    )
    out += "<p>費用率及毛利已含原有折舊／固定成本，沒有將同一成本再扣一次。未來稀釋股數的橋接、來源時刻及每項不確定性見完整模型；不是未來已核對的附註。</p>"
    out += "<h3>TTM 口徑核對</h3>"
    if trailing.get("eps_twd") is not None:
        out += (
            "<p>最近四季直接報列稀釋 EPS 加總 "
            + f"{trailing['eps_twd']:.2f}"
            + " 元；不是法定全年加權 EPS，未由年度減前三季 EPS 推出第四季。</p>"
        )
        out += table(
            ["季度", "報列稀釋 EPS", "來源定位"],
            [
                [r["period"], f"{r['eps_twd']:.2f}", r["source_id"] + "；" + r["locator"]]
                for r in trailing["quarters"]
            ],
        )
    else:
        out += (
            "<p>不可用：尚缺四季直接披露且同口徑的稀釋 EPS 原件；來源報列 PE 不代替這個分母。</p>"
        )
    out += (
        "<h3>EPS × PE 敏感度矩陣（條件價格，非合理價）</h3><p>"
        + esc(model["pe_rationale"])
        + "</p>"
    )
    out += table(
        ["情境／假設PE", *model["pe_sensitivity"]],
        [
            [name, *[f"{p:.0f}" for p in s["price_sensitivity_twd"]]]
            for name, s in model["scenarios"].items()
        ],
    )
    out += table(
        ["假設PE", "現價所需EPS和", "相對基準EPS差異%", "營收相對基準需變動%"],
        [
            [
                r["assumed_pe"],
                f"{r['required_eps_sum_twd']:.2f}",
                f"{r['required_change_vs_base_pct']:.2f}"
                if r["required_change_vs_base_pct"] is not None
                else "不可用",
                f"{r['required_revenue_growth_vs_base_pct']:.2f}"
                if r["required_revenue_growth_vs_base_pct"] is not None
                else "不可用",
            ]
            for r in model["reverse"]
        ],
    )
    out += "<p>逆推是固定其他參數的等式，不是已查證市場預期；TTM 與 Forward 的分母類型分開，歷史來源 PE 不拿來直接證明低估。</p>"
    labels = {
        "revenue_twd": "營收+10%",
        "variable_cogs_ratio": "毛利率+1百分點",
        "variable_opex_ratio": "費用率+0.5百分點",
        "tax_rate": "稅率+2百分點",
        "diluted_weighted_shares": "稀釋股數+1%",
    }
    out += "<h3>四季同向單因子敏感度（其餘不變，非機率）</h3>" + table(
        ["參數變化", "四季EPS和", "相對基準差異"],
        [
            [labels[r["driver"]], f"{r['eps_sum_twd']:.2f}", f"{r['eps_delta_twd']:+.2f}"]
            for r in model["sensitivity"]
        ],
    )
    out += (
        "<h3>主要不確定性與改判條件</h3><ul>"
        + "".join("<li>" + esc(x) + "</li>" for x in model["risks"])
        + "</ul>"
    )
    out += '<p><a href="forward-estimate.json">完整模型、假設、計算及雜湊</a>。完整官方研究／合理價資格另列，條件預測可讀不代表所有官方公告及版本已完成驗收。</p></section>'
    return out
