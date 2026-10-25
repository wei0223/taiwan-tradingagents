"""Bounded, mockable TWSE current raw-price/institution supplement; no credentials.

Vendor adjusted prices are deliberately not fabricated from exchange raw prices.
Every response, including a rejection, is kept in the returned capture receipt.
"""

import copy
import hashlib
import json
from datetime import UTC, datetime

from .calculations import institutional_metrics, technical
from .calendar import OfficialCalendarProvider, month_sessions, verify_calendar
from .provider import INSTITUTIONS, PRICE, TaiwanMarketDataProvider
from .publications import roc_date


class CurrentMarketProvider:
    price_url = "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
    institutions_url = "https://www.twse.com.tw/fund/T86"

    def __init__(self, transport=None, clock=None):
        if transport is None:
            import requests

            transport = requests.Session()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.cache, self.receipts = {}, []
        self.stopped = False

    def fetch(self, url, query):
        if self.stopped:
            raise ValueError("Official source rejected request; no further calls this run")
        key = (url, tuple(sorted(query.items())))
        if key in self.cache:
            return self.cache[key]
        response = self.transport.get(url, params=query, timeout=25, allow_redirects=False)
        raw = response.content.decode("utf-8-sig")
        receipt = {
            "source_url": url,
            "query": query,
            "retrieved_at": self.clock().isoformat(),
            "raw_response_utf8": response.content.decode("utf-8"),
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
            "http_status": response.status_code,
        }
        self.receipts.append(receipt)
        if response.status_code in {403, 429} or any(
            x in raw for x in ("查詢過量", "查詢過於頻繁", "存取遭拒")
        ):
            self.stopped = True
        if self.stopped or response.status_code != 200:
            raise ValueError("Official source unavailable; raw receipt retained")
        payload = json.loads(raw)
        if payload.get("stat") != "OK" or not payload.get("data"):
            raise ValueError("No verified official observations")
        self.cache[key] = (payload, receipt)
        return payload, receipt

    def refresh(self, snapshot):
        """Refresh only the month containing the last price, at most five new days.

        Longer outages require the existing Sponsor prepare workflow. This bounded
        operation does not rewrite old data, adjusted bases, models or forecasts.
        """
        source = copy.deepcopy(snapshot)
        if source["instrument"]["exchange"] != "twse":
            raise ValueError("TWSE supplement only; TPEx requires its own adapter")
        stock, as_of = source["instrument"]["stock_id"], source["as_of"]
        cal = source["market_calendar"]
        latest = max(r["data_date"] for r in source["datasets"][PRICE]["records"])
        query = {"date": latest[:7].replace("-", "") + "01", "response": "json"}
        payload, receipt = self.fetch(OfficialCalendarProvider.twse_url, query)
        month = datetime.strptime(query["date"], "%Y%m%d").date()
        dates = [d.isoformat() for d in month_sessions(payload, "twse", month)]
        if as_of[:7] != latest[:7] or any(d >= as_of for d in dates):
            raise ValueError("Use next-day completed-market snapshots and a bounded current month")
        new_dates = [d for d in dates if d > latest]
        if len(new_dates) > 5:
            raise ValueError("More than five missing sessions; use full prepare")
        cal["archives"] = [a for a in cal["archives"] if a["query"]["date"] != query["date"]] + [
            receipt
        ]
        cal.update(
            end=as_of,
            status="ok",
            sessions=sorted({d for d in cal["sessions"] if d[:7] != latest[:7]} | set(dates)),
        )
        verify_calendar(cal, "twse")
        price, pr = self.fetch(self.price_url, {**query, "stockNo": stock})
        if price.get("date") != query["date"] or not str(price.get("title", "")).find(stock) >= 0:
            raise ValueError("Price company/month mismatch")
        expected = ["日期", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價"]
        if price.get("fields", [])[:7] != expected:
            raise ValueError("Unexpected TWSE price schema")

        def number(v):
            return float(str(v).replace(",", ""))

        def record(dataset, raw, capture):
            period, values, units = TaiwanMarketDataProvider._normalize(dataset, raw)
            return {
                "dataset": dataset,
                "stock_id": stock,
                "data_date": raw["date"],
                "period": period,
                "source": "TWSE:" + dataset,
                "source_url": capture["source_url"],
                "retrieved_at": capture["retrieved_at"],
                "published_at": None,
                "publication_basis": "official_public_capture_only_not_initial_timestamp",
                "raw": raw,
                "raw_sha256": capture["response_sha256"],
                "values": values,
                "units": units,
            }

        rows = {}
        for row in price["data"]:
            day = roc_date(row[0]).isoformat()
            if day in new_dates:
                raw = {
                    "date": day,
                    "stock_id": stock,
                    "Trading_Volume": number(row[1]),
                    "open": number(row[3]),
                    "max": number(row[4]),
                    "min": number(row[5]),
                    "close": number(row[6]),
                }
                if day in rows:
                    raise ValueError("Duplicate official price date")
                rows[day] = record(PRICE, raw, pr)
        if set(rows) != set(new_dates):
            raise ValueError("Missing expected official price rows")
        flows = []
        for day in new_dates:
            data, capture = self.fetch(
                self.institutions_url,
                {"date": day.replace("-", ""), "selectType": "ALLBUT0999", "response": "json"},
            )
            fields = data.get("fields", [])
            if data.get("date") != day.replace("-", "") or fields[:2] != ["證券代號", "證券名稱"]:
                raise ValueError("Institution date/schema mismatch")
            matches = [r for r in data["data"] if r[0].strip() == stock]
            if len(matches) != 1:
                raise ValueError("Missing or duplicate company institution observation")
            values = dict(zip(fields, matches[0], strict=True))
            for name, buy, sell in (
                (
                    "Foreign_Investor",
                    "外陸資買進股數(不含外資自營商)",
                    "外陸資賣出股數(不含外資自營商)",
                ),
                ("Foreign_Dealer_Self", "外資自營商買進股數", "外資自營商賣出股數"),
                ("Investment_Trust", "投信買進股數", "投信賣出股數"),
                ("Dealer_self", "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)"),
                ("Dealer_Hedging", "自營商買進股數(避險)", "自營商賣出股數(避險)"),
            ):
                raw = {
                    "date": day,
                    "stock_id": stock,
                    "name": name,
                    "buy": number(values[buy]),
                    "sell": number(values[sell]),
                }
                flows.append(record(INSTITUTIONS, raw, capture))
        for name, additions in [(PRICE, list(rows.values())), (INSTITUTIONS, flows)]:
            data = source["datasets"][name]
            data["records"] += additions
            data["records"].sort(key=lambda r: (r["data_date"], r["values"].get("name", "")))
            data["raw_rows"] += [r["raw"] for r in additions]
            data["retrieved_at"] = self.clock().isoformat()
        prices = source["datasets"][PRICE]["records"]
        source["technical"] = technical(prices)
        source["institutions"] = institutional_metrics(
            source["datasets"][INSTITUTIONS]["records"], prices, "twse"
        )
        source["market_refresh"] = {
            "new_sessions": new_dates,
            "captures": copy.deepcopy(self.receipts),
            "adjusted_prices_modified": False,
            "models_modified": False,
            "basis": "raw OHLCV and institutions only; adjusted-price freshness remains a separate gate",
        }
        return source
