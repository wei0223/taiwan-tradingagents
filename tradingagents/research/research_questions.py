"""Deterministic answers to cross-dimension questions on sealed research inputs."""

import math
import re
from datetime import date
from decimal import Decimal

from .bundle import fingerprint
from .reader_research import market_alignment


def display_prose(value):
    """Round long decimal literals only in the reading layer."""
    return re.sub(
        r"(?<![A-Za-z0-9_.])(-?\d+\.\d{5,})(?![A-Za-z0-9_.])",
        lambda match: f"{Decimal(match[1]):.2f}",
        str(value).replace("−", "-"),
    )


def analyst_paragraphs(note):
    return list(
        dict.fromkeys(p.strip() for p in [note["stronger_case"], *note["paragraphs"]] if p.strip())
    )


def conclusion_parts(value):
    return [part.strip() for part in re.split(r"(?<=[。！？；])\s*|\n+", value) if part.strip()]


def moving_averages(package, weekly=False):
    if package.get("status") != "ready":
        return None
    source = package.get("weekly", {}).get("complete") if weekly else package["price_series"]["raw"]
    if not source or not source.get("bars"):
        return None
    windows = (10, 20, 40) if weekly else (5, 10, 20, 60, 200)
    close = source["bars"][-1]["close"]
    averages = []
    for window in windows:
        key = f"SMA_{window}"
        series = source["indicators"].get(key, [])
        value = series[-1] if len(series) == len(source["dates"]) else None
        if value is None or not math.isfinite(value) or value <= 0:
            continue
        previous = series[-2] if len(series) > 1 else None
        averages.append(
            {
                "window": window,
                "value": value,
                "gap_pct": (close / value - 1) * 100,
                "change": value - previous if previous is not None else None,
                "pointer": f"/indicators/{key}/{len(series) - 1}",
            }
        )
    if not averages:
        return None
    short = [a for a in averages if weekly or a["window"] != 200]
    below = sum(a["gap_pct"] < 0 for a in short)
    summary = f"{source['dates'][-1]} 收盤 {close:,.2f} 元，低於 {below}/{len(short)} 條已取得{'週' if weekly else '短中期日'}均線。"
    falling = [str(a["window"]) for a in short if a["change"] is not None and a["change"] < 0]
    if falling:
        summary += " 下彎均線為 " + "、".join(falling) + (" 週線。" if weekly else " 日線。")
    details = "、".join(
        f"{a['window']}{'週' if weekly else '日'}線 {a['value']:,.2f} 元（收盤距離 {a['gap_pct']:+.1f}%）"
        for a in averages
    )
    return {
        "date": source["dates"][-1],
        "close": close,
        "averages": averages,
        "below": below,
        "count": len(short),
        "summary": summary,
        "details": details,
        "source_pointer": "/weekly/complete" if weekly else "/price_series/raw",
        "close_pointer": f"/bars/{len(source['bars']) - 1}/close",
    }


def eps_comparison(company, cutoff):
    facts = [
        f
        for f in company.get("facts", [])
        if f.get("metric") == "diluted_eps"
        and f.get("basis") == "single_quarter"
        and f.get("scope") == "consolidated"
        and f.get("unit") == "TWD/share"
        and f["period_end"] <= cutoff
        and company["sources"][f["source_id"]]["available_from"][:10] <= cutoff
    ]
    if not facts:
        return None
    latest = max(facts, key=lambda f: f["period_end"])
    end = date.fromisoformat(latest["period_end"])
    prior = [
        f
        for f in facts
        if f["period_end"][:4] == str(end.year - 1)
        and f["period_end"][5:] == latest["period_end"][5:]
        and f["period_start"][5:] == latest["period_start"][5:]
    ]
    if len(prior) != 1:
        return None
    previous = Decimal(prior[0]["normalized_value"])
    value = Decimal(latest["normalized_value"])
    yoy = float((value / previous - 1) * 100) if previous > 0 else None
    change = f" 同比 {yoy:+.1f}%。" if yoy is not None else ""
    return {
        "fact_ids": [prior[0]["id"], latest["id"]],
        "source_sha256": [prior[0]["source_sha256"], latest["source_sha256"]],
        "periods": [prior[0]["period_end"], latest["period_end"]],
        "values": [str(previous), str(value)],
        "yoy_pct": yoy,
        "summary": f"截至 {latest['period_end']} 的單季合併稀釋 EPS 為 {value:.2f} 元，去年同期 {previous:.2f} 元。"
        + change,
        "direction": "改善" if value > previous else "轉弱" if value < previous else "持平",
    }


def flow_comparison(market):
    rows = market.get("series", [])
    if (
        market.get("status") != "available"
        or len(rows) != 20
        or market.get("price_change_pct") is None
    ):
        return None
    if any(r.get(k) is None for r in rows for k in ("foreign", "investment_trust", "return_pct")):
        return None
    foreign = sum(r["foreign"] for r in rows)
    trust = sum(r["investment_trust"] for r in rows)
    foreign_sell = [r for r in rows if r["foreign"] < 0]
    sell_down = sum(r["return_pct"] < 0 for r in foreign_sell)
    other_days = len(rows) - len(foreign_sell)
    other_down = sum(r["return_pct"] < 0 for r in rows if r["foreign"] >= 0)
    sell_rate = sell_down / len(foreign_sell) if foreign_sell else None
    other_rate = other_down / other_days if other_days else None
    delta = (sell_rate - other_rate) * 100 if sell_rate is not None and other_rate is not None else None
    comparison = (
        f"兩組下跌率為 {sell_rate:.1%} 與 {other_rate:.1%}，差 {delta:+.1f} 個百分點。"
        if delta is not None else "其中一組沒有觀測，無法比較下跌率。"
    )
    def trimmed_gap(sample):
        selling = [r for r in sample if r["foreign"] < 0]
        other = [r for r in sample if r["foreign"] >= 0]
        if not selling or not other:
            return None
        return 100 * (sum(r["return_pct"] < 0 for r in selling) / len(selling)
                      - sum(r["return_pct"] < 0 for r in other) / len(other))

    trimmed = [trimmed_gap(rows[1:]), trimmed_gap(rows[:-1])]
    sign_sensitive = delta is not None and any(
        gap is None or (gap > 0) - (gap < 0) != (delta > 0) - (delta < 0) for gap in trimmed
    )
    return {
        "sample_status": "insufficient_groups" if delta is None else (
            "small_group" if min(len(foreign_sell), other_days) < 5 else "descriptive"
        ),
        "drop_first_gap_pp": trimmed[0],
        "drop_last_gap_pp": trimmed[1],
        "sign_sensitive": sign_sensitive,
        "foreign_sell_down_rate": sell_rate,
        "other_down_rate": other_rate,
        "down_rate_gap_pp": delta,
        "flow_units": {"raw_unit": "股", "display_unit": "張", "conversion_factor": 0.001, "shares_per_lot": 1000,
                       "display_rounding": "整張，半值取偶；原始股數完整保存"},
        "observations": [
            {key: row.get(key) for key in ("date", "foreign", "investment_trust", "return_pct")}
            for row in rows
        ],
        "start": market["start"],
        "end": market["end"],
        "sessions": len(rows),
        "price_change_pct": market["price_change_pct"],
        "foreign_net_shares": foreign,
        "trust_net_shares": trust,
        "foreign_sell_days": len(foreign_sell),
        "foreign_sell_down_days": sell_down,
        "other_days": other_days,
        "other_down_days": other_down,
        "trust_buy_days": market["trust_buy_days"],
        "trust_buy_down_days": market["trust_buy_down_days"],
        "summary": f"{market['start']} 至 {market['end']} 的 20 個交易日，股價變動 {market['price_change_pct']:+.1f}%，"
        f"外資淨額 {foreign / 1000:+,.0f} 張、投信淨額 {trust / 1000:+,.0f} 張。"
        f"外資賣超 {len(foreign_sell)} 日，其中 {sell_down} 日股價下跌。其餘 {other_days} 日中有 {other_down} 日下跌。 {comparison}",
    }


def answer(relations, company, technical, snapshot):
    eps = eps_comparison(company, snapshot["as_of"])
    trend = moving_averages(technical)
    flow = flow_comparison(market_alignment(snapshot))
    answers = []
    cross_evidence = {}
    for method, measure in (("aligned_flow_20", flow), ("reported_eps_yoy", eps), ("moving_average_structure", trend)):
        if measure is not None:
            item = {"method": method, "stock_id": snapshot.get("instrument", {}).get("stock_id"),
                    "as_of": snapshot["as_of"], "measurement": measure}
            cross_evidence[method] = {**item, "evidence_id": "CE-" + fingerprint(item)[:20]}
    for relation in relations:
        dimensions = set(relation["dimensions"])
        market_pair = dimensions == {"technical", "institutional"}
        if not market_pair and (relation["materiality"] == "minor" or relation["relationship"] == "insufficient"):
            continue
        if dimensions == {"fundamental", "technical"}:
            question = "最近可比的單季稀釋 EPS 如何變化？最新收盤位於多少條短中期均線之下？"
            measures = {"eps": eps, "trend": trend}
            limit = "季度獲利與最新行情屬不同期間。缺少可核對的歷史 EPS 時點序列及公告事件基準，估值變化與公告反應未驗證。"
        elif dimensions == {"fundamental", "institutional"}:
            question = "最近可比季度的獲利方向，與近 20 日外資、投信的淨流向是否一致？"
            measures = {"eps": eps, "flow": flow}
            limit = "季度獲利與法人觀測窗不同，資料可界定短期供需壓力，法人動機與盈利預期仍待查證。"
        elif dimensions == {"technical", "institutional"}:
            question = "同一組 20 個交易日內，外資賣超與股價下跌有幾日同時發生？投信淨額方向為何？"
            measures = {"flow": flow}
            limit = "同日共現只作描述，尚未調整市場基準。"
            if flow and flow["sample_status"] == "small_group":
                limit += "至少一組少於 5 日，證據強度低。"
            if flow and flow["sign_sensitive"]:
                limit += "移除首日或末日後，方向改變或無法比較。"
            if flow and flow["sample_status"] == "insufficient_groups":
                limit += "缺少其中一組交易日，無法比較下跌率。"
        else:
            continue
        observed = []
        if eps is not None and "fundamental" in dimensions:
            observed.append(f"已公布的可比季度稀釋 EPS {eps['direction']}。")
        if flow is not None and dimensions == {"technical", "institutional"}:
            gap = flow["down_rate_gap_pp"]
            if gap is not None:
                observed.append(
                    f"外資賣超日下跌率較其他日{'高' if gap > 0 else '低'} {abs(gap):.1f} 個百分點。"
                    if gap else "外資賣超日與其他日的下跌率相同。"
                )
        if not observed:
            observed = [item["summary"] for item in measures.values() if item is not None]
        if eps and trend and dimensions == {"fundamental", "technical"} and trend["count"]:
            observed.append(
                f"收盤低於 {trend['below']}/{trend['count']} 條短中期均線，支持價格承壓。"
                if trend["below"] > trend["count"] / 2
                else "收盤低於的短中期均線未過半。"
            )
        if (
            flow
            and "institutional" in dimensions
            and flow["foreign_net_shares"] < 0 < flow["trust_net_shares"]
        ):
            observed.append("外資淨賣、投信淨買，顯示法人流向分化。")
        missing = [key for key, item in measures.items() if item is None]
        if market_pair and flow is not None and flow["down_rate_gap_pp"] is None:
            missing.append("comparison_group")
        answers.append(
            {
                "id": relation["id"] + ".calculation",
                "issue_id": relation["id"],
                "prior_materiality": relation["materiality"],
                "evidence_strength": (
                    "unavailable" if not observed else "limited_descriptive"
                    if market_pair and (missing or flow["sample_status"] != "descriptive" or flow["sign_sensitive"])
                    else "descriptive" if market_pair else "context_only"
                ),
                "question": question,
                "required_data": list(measures),
                "method": (
                    "比較相同交易窗中外資淨賣與其餘交易日的下跌率"
                    if dimensions == {"technical", "institutional"} else "比較已公布單季同比與另一面向的觀測方向"
                ),
                "finding_kind": (
                    "descriptive_new_statistic"
                    if dimensions == {"technical", "institutional"} and flow and flow["down_rate_gap_pp"] is not None
                    else "context_comparison" if observed else "unavailable"
                ),
                "status": "answered" if not missing else "partial" if observed else "unavailable",
                "evidence_ids": [
                    cross_evidence[method]["evidence_id"]
                    for key, method in (("eps", "reported_eps_yoy"), ("trend", "moving_average_structure"),
                                        ("flow", "aligned_flow_20"))
                    if measures.get(key) is not None
                ],
                "missing": missing,
                "answer": " ".join(observed) or "缺少符合口徑的觀測。",
                "limit": limit,
            }
        )
    used = {eid for row in answers for eid in row["evidence_ids"]}
    return {"answers": answers, "trend": trend, "flow": flow,
            "cross_evidence_result": [row for row in cross_evidence.values() if row["evidence_id"] in used]}
