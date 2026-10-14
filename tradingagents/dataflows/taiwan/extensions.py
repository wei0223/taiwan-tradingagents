"""Optional Taiwan research inputs. Current captures never impersonate past vintages."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from tradingagents.dataflows.errors import VendorUnavailableError

from .calculations import technical
from .provider import PRICE, DataResult, TaiwanMarketDataProvider, day, number
from .publications import BALANCE, CASHFLOW, TAIPEI, digest, instant, validate_publication

VALUATION = "TaiwanStockPER"
ADJUSTED = "TaiwanStockPriceAdj"
NEWS = "TaiwanStockNews"
DIVIDEND = "TaiwanStockDividendResult"
REDUCTION = "TaiwanStockCapitalReductionReferencePrice"
SPLIT = "TaiwanStockSplitPrice"
PAR_VALUE = "TaiwanStockParValueChange"
SUPPLEMENTS = (VALUATION, CASHFLOW, BALANCE, ADJUSTED, NEWS, DIVIDEND, REDUCTION, SPLIT, PAR_VALUE)
ACTION_DATASETS = (DIVIDEND, REDUCTION, SPLIT, PAR_VALUE)


def capture_day(timestamp):
    return instant(timestamp).astimezone(TAIPEI).date().isoformat()


def text(value, maximum=600):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError("Invalid source text")
    return re.sub(r"[\x00-\x1f]", " ", value).strip()


def normalize(dataset, row):
    """No quantity is inferred from prose. Amounts retain vendor statement periods."""
    if dataset == ADJUSTED:
        period, values, _ = TaiwanMarketDataProvider._normalize(PRICE, row)
        return period, values, {"price": "TWD vendor adjusted", "volume": "shares"}
    if dataset == VALUATION:
        values = {name: number(row[key]) for name, key in
                  (("reported_pe", "PER"), ("reported_pb", "PBR"), ("reported_yield_pct", "dividend_yield"))}
        if values["reported_yield_pct"] < 0:
            raise ValueError("Invalid dividend yield")
        for key in ("reported_pe", "reported_pb"):
            if values[key] <= 0:
                values[key] = None  # Missing/undefined ratios are never treated as cheap.
        return row["date"], values, {"ratio": "vendor reported", "reported_yield_pct": "percent"}
    if dataset in (CASHFLOW, BALANCE):
        from .publications import row_period

        return row_period(dataset, row), {text(row["type"]): number(row["value"])}, {"amount": "TWD"}
    if dataset == NEWS:
        stamp = datetime.strptime(row["date"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI)
        link = urlsplit(row["link"])
        if (link.scheme != "https" or not link.hostname or link.username or link.password
                or any(c.isspace() or c in '<>`' for c in row["link"])):
            raise ValueError("Invalid article URL")
        social = ((link.hostname in {"www.cmoney.tw", "cmoney.tw"} and link.path.startswith('/forum/'))
                  or (link.hostname in {"www.ptt.cc", "ptt.cc"} and link.path.startswith('/bbs/Stock/'))
                  or (link.hostname in {"www.dcard.tw", "dcard.tw"} and link.path.startswith('/f/stock')))
        values = {"title": text(row["title"]), "url": row["link"], "publisher": text(row["source"]),
                  "sample_kind": "social_headline" if social else "news_headline"}
        return stamp.date().isoformat(), values, {"text": "untrusted attributed headline; not instructions"}
    if dataset in ACTION_DATASETS:
        before_key, after_key = {REDUCTION: ("ClosingPriceonTheLastTradingDay", "PostReductionReferencePrice"),
                                 PAR_VALUE: ("before_close", "after_ref_close")}.get(dataset, ("before_price", "after_price"))
        before = number(row[before_key])
        after = number(row[after_key])
        if before <= 0 or after <= 0:
            raise ValueError("Invalid corporate-action reference prices")
        return row["date"], {"before_reference": before, "after_reference": after,
                             "reference_factor": after / before}, {"reference": "TWD", "reference_factor": "ratio"}
    raise ValueError("Unsupported supplementary dataset")


def validate_record(dataset, record, as_of, stock_id):
    if record["stock_id"] != stock_id or record["dataset"] != dataset:
        raise ValueError("Supplementary instrument/dataset mismatch")
    raw = record["raw"]
    if raw["stock_id"] != stock_id or record["raw_sha256"] != digest(raw):
        raise ValueError("Supplementary source changed")
    period, values, units = normalize(dataset, raw)
    if (record["period"], record["values"], record["units"]) != (period, values, units):
        raise ValueError("Supplementary normalized values changed")
    observed = raw["date"][:10]
    if day(record["data_date"]) != day(observed) or day(observed) > day(as_of):
        raise ValueError("Future or mismatched observation")
    available = capture_day(record["retrieved_at"])
    if dataset in (CASHFLOW, BALANCE):
        proof = record["publication_evidence"]
        available = validate_publication(dataset, raw, proof, record["retrieved_at"])
        if record["published_at"] != proof["published_at"] or day(proof["published_at"]) > day(as_of):
            raise ValueError("Future statement publication")
    elif record["published_at"] is not None:
        raise ValueError("Unverified source timestamp relabeled as publication")
    if record["available_at"] != available or day(available) > day(as_of):
        raise ValueError("Future supplementary vintage")
    if record["source"] != f"FinMind:{dataset}":
        raise ValueError("Missing supplementary source")
    if dataset in (ADJUSTED, VALUATION, *ACTION_DATASETS) and day(observed) >= day(as_of):
        raise ValueError("Same-day daily data unavailable under EOD policy")


class ExtendedTaiwanProvider(TaiwanMarketDataProvider):
    def __init__(self, client=None, publication_resolver=None, calendar_provider=None, news_days=3):
        super().__init__(client, publication_resolver)
        if not isinstance(news_days, int) or isinstance(news_days, bool) or not 1 <= news_days <= 31:
            raise ValueError("news_days must be in 1..31")
        self.news_days = news_days
        self.calendar_provider = calendar_provider

    def supplementary(self, dataset, stock_id, start, as_of):
        if dataset not in SUPPLEMENTS or day(start) > day(as_of):
            raise ValueError("Invalid supplementary query")
        query = {"data_id": stock_id, "start_date": start, "end_date": as_of}
        result = DataResult(dataset, stock_id, as_of, query, self.client.now())
        try:
            if dataset == NEWS:
                dates = [max(day(start), day(as_of) - timedelta(days=self.news_days)) + timedelta(days=i)
                         for i in range(self.news_days)]
                dates = sorted({d for d in dates if day(start) <= d < day(as_of)})
                result.query["requested_days"] = [str(d) for d in dates]
                for observed in dates:
                    rows, retrieved = self.client.query(dataset, data_id=stock_id, start_date=str(observed))
                    result.raw_rows.extend(rows)
                    result.retrieved_at = max(result.retrieved_at, retrieved)
            else:
                if dataset == PAR_VALUE:
                    # Official documentation exposes this as a whole-market feed.
                    result.query = {"start_date": start, "end_date": as_of, "scope": "all_market_filtered_by_stock_id"}
                    result.raw_rows, result.retrieved_at = self.client.query(dataset, start_date=start, end_date=as_of)
                else:
                    result.raw_rows, result.retrieved_at = self.client.query(dataset, **query)
        except VendorUnavailableError:
            result.warnings.append("Supplementary API unavailable; do not infer missing values")
            return result.to_dict()
        seen, conflicts = {}, set()
        for raw in result.raw_rows:
            try:
                if dataset == PAR_VALUE and raw.get("stock_id") != stock_id:
                    continue
                if raw.get("stock_id") != stock_id:
                    raise ValueError("Source company mismatch")
                observed = raw["date"][:10]
                if not day(start) <= day(observed) <= day(as_of):
                    continue
                if dataset != NEWS and day(observed) >= day(as_of):
                    continue
                period, values, units = normalize(dataset, raw)
                if dataset == NEWS and day(period) >= day(as_of):
                    continue
                proof = None
                available = capture_day(result.retrieved_at)
                if dataset in (CASHFLOW, BALANCE):
                    proof = self.publication_resolver(dataset, raw) if self.publication_resolver else None
                    if not isinstance(proof, dict):
                        result.warnings.append(f"{observed}: verified statement publication missing")
                        continue
                    available = validate_publication(dataset, raw, proof, result.retrieved_at)
                    if day(proof["published_at"]) > day(as_of):
                        result.warnings.append(f"{observed}: publication after as_of")
                        continue
                if day(available) > day(as_of):
                    result.warnings.append(f"{observed}: numeric/text vintage captured after as_of")
                    continue
                key = (raw["date"] if dataset == NEWS else period, raw.get("type"), values.get("url"))
                if key in conflicts:
                    continue
                if key in seen:
                    if dataset == NEWS and seen[key] == digest(raw):
                        continue
                    if dataset == NEWS:
                        conflicts.add(key)
                        result.records = [r for r in result.records if (r["raw"]["date"], None, r["values"]["url"]) != key]
                        result.warnings.append("Conflicting title variants for the same URL/timestamp excluded")
                        continue
                    raise ValueError("Duplicate period; ambiguous vintage")
                seen[key] = digest(raw)
                record = {"dataset": dataset, "stock_id": stock_id, "period": period, "data_date": observed,
                          "published_at": proof["published_at"] if proof else None,
                          "available_at": available, "retrieved_at": result.retrieved_at,
                          "source": f"FinMind:{dataset}", "raw": raw, "raw_sha256": digest(raw),
                          "values": values, "units": units}
                if proof:
                    record["publication_evidence"] = proof
                validate_record(dataset, record, as_of, stock_id)
                result.records.append(record)
            except (ValueError, KeyError, TypeError, OverflowError):
                if dataset == NEWS:
                    result.warnings.append("Invalid headline row excluded; raw retained for audit")
                    continue
                result.records.clear()
                result.warnings.append("Invalid supplementary schema or duplicate; dataset excluded")
                break
        result.records.sort(key=lambda row: (row["raw"]["date"], row["raw"].get("type", ""), row["values"].get("url", "")))
        relevant_raw = [r for r in result.raw_rows if dataset != PAR_VALUE or r.get("stock_id") == stock_id]
        if dataset in ACTION_DATASETS and not relevant_raw:
            if capture_day(result.retrieved_at) > as_of:
                result.warnings.append("Absence query captured after analysis date; unavailable")
                return result.to_dict()
            return {**result.to_dict(), "status": "ok", "absence_verified": True,
                    "absence_basis": "successful API returned no reported events in requested window"}
        if not result.records:
            result.warnings.append("NO_DATA_AVAILABLE; never fabricate")
        return result.to_dict()

    def snapshot(self, symbol, start_date, as_of):
        snapshot = super().snapshot(symbol, start_date, as_of)
        stock_id = snapshot["instrument"]["stock_id"]
        snapshot["extensions"] = {dataset: self.supplementary(dataset, stock_id, start_date, as_of)
                                  for dataset in SUPPLEMENTS}
        if hasattr(self.publication_resolver, "audit"):
            snapshot["publication_audit"] = self.publication_resolver.audit(stock_id)
        if self.calendar_provider:
            snapshot["market_calendar"] = self.calendar_provider.fetch(
                snapshot["instrument"]["exchange"], start_date, as_of)
        snapshot["extended_research"] = extension_metrics(snapshot)
        snapshot["limitations"] += [
            "News/forum headlines are attributed samples, not independently verified facts or representative sentiment",
            "Cash flow amounts retain the source statement period; do not relabel cumulative cash flow as single-quarter",
            "Vendor adjusted history is retrospective and usable only from its capture date",
            "Forward estimates/guidance remain unavailable unless independently supplied; no fair price inferred",
        ]
        return snapshot


POSITIVE = ("成長", "創高", "上修", "看好", "利多", "增加", "突破")
NEGATIVE = ("衰退", "下修", "利空", "下跌", "賣超", "減少", "風險", "衝擊")


def headline_sentiment(records, kind):
    unique = {}
    for record in sorted(records, key=lambda r: r["raw"]["date"]):
        value = record["values"]
        if value["sample_kind"] == kind:
            # One URL contributes once, using its latest observed title; syndicated URLs
            # are additionally deduplicated by identical whitespace-normalized title.
            unique[value["url"]] = value["title"]
    unique = {re.sub(r"\s+", "", title): title for title in unique.values()}
    scores = []
    for title in unique.values():
        score = sum(word in title for word in POSITIVE) - sum(word in title for word in NEGATIVE)
        scores.append(1 if score > 0 else -1 if score < 0 else 0)
    return {"sample_count": len(scores), "positive_count": scores.count(1),
            "negative_count": scores.count(-1), "neutral_count": scores.count(0),
            "lexical_mean": sum(scores) / len(scores) if scores else None,
            "method": "fixed_zh_headline_lexicon_v1; not investor positioning or factual verification"}


def reference_adjusted(prices, actions):
    """Reference-factor comparison, not a total-return reinvestment model."""
    result = copy.deepcopy(prices)
    events = {}
    for event in actions:
        if event["data_date"] in events:
            raise ValueError("Overlapping corporate actions; cannot choose a factor")
        events[event["data_date"]] = event["values"]["reference_factor"]
    for record in result:
        factor = 1.0
        for event_date, ratio in sorted(events.items()):
            if record["data_date"] < event_date:
                factor *= ratio
        for name in ("open", "high", "low", "close"):
            record["values"][name] = number(record["values"][name] * factor)
    return result


def extension_metrics(snapshot):
    data = snapshot.get("extensions", {})
    records = {key: sorted(value["records"], key=lambda row: (row["period"], row["raw"].get("type", ""),
                                                            row["values"].get("url", "")))
               for key, value in data.items()}
    ratios = records.get(VALUATION, [])
    latest_ratio = ratios[-1]["values"] if ratios else {}
    pe_window = [row["values"]["reported_pe"] for row in ratios if row["values"]["reported_pe"] is not None]
    cash = {}
    for row in records.get(CASHFLOW, []):
        cash.setdefault(row["period"], {}).update(row["values"])
    flows = []
    for period, values in sorted(cash.items()):
        operating = values.get("CashFlowsFromOperatingActivities")
        ppe = values.get("PropertyAndPlantAndEquipment")
        flows.append({"period": period, "operating_twd_as_reported": operating,
                      "ppe_cash_outflow_twd_as_reported": -ppe if ppe is not None and ppe <= 0 else None,
                      "operating_less_ppe_twd_as_reported": operating + ppe
                      if operating is not None and ppe is not None and ppe <= 0 else None,
                      "basis": "source statement period; not certified single-quarter/TTM free cash flow"})
    adjusted = records.get(ADJUSTED, [])
    adjusted_metrics = technical(adjusted) if adjusted else None
    if adjusted_metrics:
        adjusted_metrics["warnings"] = ["Vendor backward-adjusted capture; volumes remain reported shares"]
    reference_metrics, reference_warning = None, None
    if all(data.get(key, {}).get("status") == "ok" for key in ACTION_DATASETS):
        actions = [row for key in ACTION_DATASETS for row in records[key]]
        try:
            reference_metrics = technical(reference_adjusted(snapshot["datasets"][PRICE]["records"], actions))
            reference_metrics["warnings"] = ["Computed event reference-factor comparison; not dividend reinvestment or a share/cash ledger"]
        except ValueError:
            reference_warning = "Overlapping events; reference adjustment unavailable rather than double counted"
    return {"valuation": {**latest_ratio,
                           "reported_pe_window_percentile_pct":
                           sum(value <= latest_ratio["reported_pe"] for value in pe_window) / len(pe_window) * 100
                           if pe_window and latest_ratio.get("reported_pe") else None,
            "reported_pe_earnings_yield_pct": 100 / latest_ratio["reported_pe"]
                           if latest_ratio.get("reported_pe") else None,
                           "forward_pe": None, "fair_value": None},
            "cashflow": flows, "adjusted_technical": adjusted_metrics,
            "reference_adjusted_technical": reference_metrics, "reference_adjusted_warning": reference_warning,
            "news_sample_sentiment": headline_sentiment(records.get(NEWS, []), "news_headline"),
            "social_sample_sentiment": headline_sentiment(records.get(NEWS, []), "social_headline")}


def add_extended_evidence(snapshot, add, coverage):
    """Validate raw-bound supplements, recompute all derived values, then append evidence."""
    if "extensions" not in snapshot:
        return []
    stock_id, as_of = snapshot["instrument"]["stock_id"], snapshot["as_of"]
    data = snapshot["extensions"]
    if set(data) != set(SUPPLEMENTS):
        raise ValueError("Extended research profile has unexpected/missing datasets")
    for dataset, result in data.items():
        if result["status"] not in {"ok", "partial", "unavailable"}:
            raise ValueError("Invalid extended dataset status")
        if result["status"] == "unavailable" and result["records"]:
            raise ValueError("Unavailable extended dataset contains records")
        relevant_raw = [r for r in result["raw_rows"] if dataset != PAR_VALUE or r.get("stock_id") == stock_id]
        if result.get("absence_verified") and (dataset not in ACTION_DATASETS or relevant_raw
                or capture_day(result["retrieved_at"]) > as_of):
            raise ValueError("Invalid/future absence evidence")
        if result["status"] == "ok" and not result["records"] and not result.get("absence_verified"):
            raise ValueError("Empty extended dataset marked successful")
        keys = set()
        for record in result["records"]:
            validate_record(dataset, record, as_of, stock_id)
            key = (record["raw"]["date"] if dataset == NEWS else record["period"],
                   record["raw"].get("type"), record["values"].get("url"))
            if key in keys:
                raise ValueError("Duplicate supplementary source")
            keys.add(key)
        coverage[dataset] = {"status": result["status"], "eligible_rows": len(result["records"]),
                             "warnings": sorted(set(result["warnings"])),
                             "sources": sorted({r["source"] for r in result["records"]}),
                             "retrieved_at": [result["retrieved_at"]]}
    price_days = {r["data_date"] for r in snapshot["datasets"][PRICE]["records"]}
    adjusted_days = {r["data_date"] for r in data[ADJUSTED]["records"]}
    if adjusted_days and adjusted_days != price_days:
        coverage[ADJUSTED]["status"] = "partial"
        coverage[ADJUSTED]["warnings"].append("Adjusted/raw observation coverage differs; complete history unverified")
    metrics = extension_metrics(snapshot)
    ratios = data[VALUATION]["records"]
    for key, value in sorted(metrics["valuation"].items()):
        add(f"valuation.{key}", value, "percent" if key.endswith("pct") else "ratio", VALUATION, ratios[-1:])
    for record in data[BALANCE]["records"][-250:]:
        for key, value in sorted(record["values"].items()):
            if key in {"CashAndCashEquivalents", "TotalAssets", "Liabilities", "CurrentAssets", "CurrentLiabilities", "Equity"}:
                add(f"balance.{record['period']}.{key}", value, "TWD", BALANCE, [record])
    for metric in metrics["cashflow"][-8:]:
        subset = [r for r in data[CASHFLOW]["records"] if r["period"] == metric["period"]]
        for key, value in sorted(metric.items()):
            if key not in {"basis", "period"}:
                add(f"cashflow.{metric['period']}.{key}", value, "TWD source statement period", CASHFLOW, subset)
    adjusted = data[ADJUSTED]["records"]
    adjusted_metrics = metrics["adjusted_technical"]
    if adjusted_metrics:
        for period, value in adjusted_metrics["ma"].items():
            add(f"adjusted_technical.ma.{period}", value, "TWD vendor adjusted", ADJUSTED, adjusted[-int(period):])
        for key in ("rsi_14", "macd", "macd_signal", "macd_histogram"):
            add(f"adjusted_technical.{key}", adjusted_metrics[key], "indicator", ADJUSTED, adjusted)
    for kind, label in (("news_headline", "news"), ("social_headline", "social")):
        subset = [r for r in data[NEWS]["records"] if r["values"]["sample_kind"] == kind]
        # Only attributed titles/URLs enter the model; no HTML descriptions or raw feeds.
        for index, record in enumerate(subset[-20:]):
            add(f"{label}.headline.{index}", record["values"]["title"], "attributed untrusted headline", NEWS, [record])
        sentiment = headline_sentiment(subset, kind)
        for key in ("sample_count", "positive_count", "negative_count", "neutral_count", "lexical_mean"):
            add(f"{label}.sentiment.{key}", sentiment[key] if subset else None,
                "lexical sample statistic", NEWS, subset)
    for dataset in ACTION_DATASETS:
        for record in data[dataset]["records"]:
            add(f"actions.{dataset}.{record['period']}.reference_factor", record["values"]["reference_factor"],
                "reference-price factor; not share ratio or cash payout", dataset, [record])
    reference = metrics["reference_adjusted_technical"]
    if reference:
        inputs = snapshot["datasets"][PRICE]["records"] + [row for key in ACTION_DATASETS for row in data[key]["records"]]
        for period, value in reference["ma"].items():
            add(f"reference_adjusted_technical.ma.{period}", value, "TWD computed reference-adjusted", PRICE, inputs)
        for key in ("rsi_14", "macd", "macd_signal", "macd_histogram"):
            add(f"reference_adjusted_technical.{key}", reference[key], "indicator", PRICE, inputs)
    calendar = snapshot.get("market_calendar")
    if calendar:
        from .calculations import institutional_metrics
        from .calendar import verify_calendar
        from .provider import INSTITUTIONS

        sessions = verify_calendar(calendar, snapshot["instrument"]["exchange"])
        if calendar["status"] == "ok":
            observed_prices = {r["data_date"] for r in snapshot["datasets"][PRICE]["records"]}
            if not observed_prices <= set(sessions):
                raise ValueError("Price observations occur outside verified market sessions")
            institutions = snapshot["datasets"][INSTITUTIONS]["records"]
            market_metrics = institutional_metrics(institutions, [{"data_date": d} for d in sessions],
                                                   snapshot["instrument"]["exchange"])
            for group, metric in market_metrics["groups"].items():
                for key in ("latest_net_shares", "signed_days", "streak_net_shares", "window_net_shares"):
                    add(f"calendar_verified_institutions.{group}.{key}", metric[key],
                        "official sessions" if key == "signed_days" else "shares", INSTITUTIONS, institutions)
    return [name for name in (VALUATION, CASHFLOW, BALANCE, ADJUSTED) if coverage[name]["status"] != "ok"]
