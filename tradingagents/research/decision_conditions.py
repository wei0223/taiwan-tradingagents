"""A one-shot observation with explicitly qualified hypothetical trade economics."""

from .scenarios import finite


def build(data, company, setup=None):
    stock, as_of = data["stock_id"], data["as_of"]
    check = data["reader_research"].get("short_term_check") or {"status": "unavailable"}
    latest = data["dynamics"].get("latest_session")
    window = data["dynamics"].get("windows", {}).get("5", {})
    foreign = window.get("institutions", {}).get("foreign", {})
    aligned = (latest is not None and check.get("date") == latest == window.get("end")
               and window.get("status") == "available" and foreign.get("coverage_complete") is True)
    net = foreign.get("window_net_shares") if aligned else None
    flow = {"status": "unavailable" if net is None else "passed" if finite(net) > 0 else "failed",
            "window_sessions": 5, "from": window.get("start") if aligned else None,
            "through": latest if aligned else None, "foreign_net_shares": net,
            "rule": "完整且同截止日的五交易日外資合計須大於零"}
    facts = {f["id"]: f for f in (company or {}).get("facts", [])}
    fundamental = {"status": "unavailable", "rule": "以指定季度已核直接稀釋EPS與門檻核對；新公告或更正後重驗",
                   "major_event_review": "required_on_new_filing_or_correction"}
    economics = None
    if setup is not None:
        if (setup.get("stock_id"), setup.get("as_of")) != (stock, as_of):
            raise ValueError("Trade scenario company/cutoff mismatch")
        if not isinstance(setup.get("rationale"), str) or not setup["rationale"].strip():
            raise ValueError("Trade scenario requires a rationale for the hypothetical levels and costs")
        earnings = setup.get("earnings", {})
        fact = facts.get(earnings.get("fact_id"), {})
        eligible = [f for f in facts.values() if f.get("kind") == "numeric"
                    and f.get("metric") == "diluted_eps" and f.get("derivation") == "direct"
                    and f.get("unit") == "TWD/share" and f.get("scope") == "consolidated"
                    and f.get("basis") == "single_quarter" and f.get("period_end", "9999") <= as_of]
        if fact in eligible and fact["period_end"] == max(f["period_end"] for f in eligible):
            value = finite(float(fact["normalized_value"]))
            minimum = finite(earnings["minimum_eps_twd"])
            fundamental.update(status="passed" if value >= minimum else "failed",
                               fact_id=fact["id"], period_end=fact["period_end"],
                               eps_twd=value, minimum_eps_twd=minimum,
                               source_sha256=fact["source_sha256"])
        entry, stop, target = (finite(setup[k]) for k in ("entry_twd", "invalidation_twd", "target_twd"))
        buy, sell = (finite(setup[k]) for k in ("entry_cost_bps", "exit_cost_bps"))
        if not 0 < stop < entry < target or not (0 <= buy < 10000 and 0 <= sell < 10000):
            raise ValueError("Explicit long levels and nonnegative per-leg cost assumptions required")
        entry_cost = entry * (1 + buy / 10000)
        exit_factor = 1 - sell / 10000
        reward = target * exit_factor - entry_cost
        loss = entry_cost - stop * exit_factor
        economics = {"entry_twd": entry, "invalidation_twd": stop, "target_twd": target,
                     "entry_cost_bps": buy, "exit_cost_bps": sell,
                     "net_reward_twd_per_share": reward, "net_loss_twd_per_share": loss,
                     "net_reward_risk": reward / loss if reward > 0 else None,
                     "rationale": setup["rationale"], "kind": "hypothetical_unfilled"}
    technical_ready = latest is not None and check.get("date") == latest and check["status"] == "confirmed"
    reasons = []
    if not technical_ready:
        reasons.append("price_volume_or_weekly_confirmation_missing")
    if flow["status"] != "passed":
        reasons.append("institutional_confirmation_missing")
    if fundamental["status"] != "passed":
        reasons.append("earnings_condition_unconfirmed")
    if economics is None or economics["net_reward_risk"] is None:
        reasons.append("hypothetical_levels_or_positive_net_reward_missing")
    return {
        "schema_version": 1, "stock_id": stock, "as_of": as_of,
        "status": "observe" if reasons else "conditional_candidate", "reason_codes": reasons,
        "technical": check, "institutional": flow, "fundamental": fundamental,
        "hypothetical": economics, "market_session": latest,
        "validation_windows": [{"sessions": days, "status": "pending" if not reasons else "not_applicable",
                                "anchor": "first_official_session_after_confirmation",
                                "expiry": "close_of_window_last_official_session"} for days in (5, 20)],
        "invalidation_rules": [
            "收盤跌破封存的觀察失效門檻，或跌破明示情境損失界線。",
            "新財報、更正或重大公告使原獲利條件無法成立或需要重新審查。",
            "當日交易所時點、價量或法人資料缺漏時，候選維持觀察。",
        ],
        "execution": False, "backtest_edge_verified": False,
        "long_term_valuation_ref": "valuation-scenarios.json",
    }


def display_rows(conditions):
    trade = conditions["hypothetical"]
    economics = "缺少同次研究的明示價位及成本假設"
    if trade:
        ratio = trade["net_reward_risk"]
        rr = f"{ratio:.2f}R" if ratio is not None else "扣成本後無正報酬"
        economics = (f"假設進場 {trade['entry_twd']:,.2f} 元、失效 {trade['invalidation_twd']:,.2f} 元、"
                     f"目標 {trade['target_twd']:,.2f} 元；買／賣成本 {trade['entry_cost_bps']:g}／"
                     f"{trade['exit_cost_bps']:g} 基點；{rr}")
    return [
        ["價格與量能", conditions["technical"]["status"], "前 20 日高點、量比及同週完整週收盤"],
        ["法人", conditions["institutional"]["status"], conditions["institutional"]["rule"]],
        ["獲利", conditions["fundamental"]["status"], conditions["fundamental"]["rule"]],
        ["交易情境", conditions["status"], economics],
        ["驗證期限", "5、20 個交易日", "符合所有條件後，從下一個官方交易日計起。"],
    ]
