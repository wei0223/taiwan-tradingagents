"""Additive, mockable FinMind adapter. Unknown financial publication dates fail closed."""

from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Protocol

from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError

PRICE = "TaiwanStockPrice"
REVENUE = "TaiwanStockMonthRevenue"
FINANCIAL = "TaiwanStockFinancialStatements"
INSTITUTIONS = "TaiwanStockInstitutionalInvestorsBuySell"
DATASETS = (PRICE, REVENUE, FINANCIAL, INSTITUTIONS)


def day(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("Dates must use YYYY-MM-DD")
    return parsed


def number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Boolean is not a numeric value")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite numeric value")
    return result


@dataclass(frozen=True)
class Instrument:
    stock_id: str
    exchange: str
    name: str
    yahoo_symbol: str
    metadata_date: str
    retrieved_at: str
    source: str = "FinMind:TaiwanStockInfo"
    historical_membership_verified: bool = False
    raw_metadata: dict = field(default_factory=dict)


@dataclass
class DataResult:
    dataset: str
    stock_id: str
    as_of: str
    query: dict
    retrieved_at: str
    records: list[dict] = field(default_factory=list)
    raw_rows: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.records:
            return "unavailable"
        return "partial" if self.warnings else "ok"

    def to_dict(self) -> dict:
        return {**asdict(self), "status": self.status}


class MarketDataProvider(Protocol):
    def resolve_symbol(self, symbol: str) -> Instrument: ...

    def fetch(self, dataset: str, symbol: str, start_date: str, as_of: str) -> DataResult: ...

    def snapshot(self, symbol: str, start_date: str, as_of: str) -> dict: ...


class FinMindClient:
    endpoint = "https://api.finmindtrade.com/api/v4/data"

    def __init__(self, transport=None, clock=None):
        # Lazy import keeps calculation-only callers free of network initialization.
        if transport is None:
            import requests

            transport = requests.Session()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))

    def now(self) -> str:
        instant = self.clock()
        if instant.tzinfo is None:
            raise ValueError("clock must return a timezone-aware datetime")
        return instant.astimezone(UTC).isoformat()

    def query(self, dataset: str, **params) -> tuple[list[dict], str]:
        token = os.environ.get("FINMIND_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self.transport.get(
                self.endpoint,
                params={"dataset": dataset, **params},
                headers=headers,
                timeout=20,
            )
            if response.status_code != 200:
                raise VendorUnavailableError(f"FinMind HTTP status {response.status_code}")
            payload = response.json()
        except VendorUnavailableError:
            raise
        except Exception:
            # Do not expose request URLs, headers, or vendor message text containing secrets.
            raise VendorUnavailableError("FinMind transport or JSON decoding failed") from None
        if not isinstance(payload, dict) or payload.get("status") != 200:
            raise VendorUnavailableError("FinMind API did not return success")
        rows = payload.get("data")
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise VendorUnavailableError("FinMind data schema is invalid")
        return rows, self.now()


class TaiwanMarketDataProvider:
    def __init__(self, client: FinMindClient | None = None, publication_resolver=None):
        self.client = client or FinMindClient()
        # A verified archive/MOPS adapter can supply publication dates. FinMind's
        # date/create_time fields are deliberately never treated as publication.
        self.publication_resolver = publication_resolver

    def resolve_symbol(self, symbol: str) -> Instrument:
        match = re.fullmatch(r"([0-9]{4,6})(\.TW|\.TWO)?", symbol.strip().upper())
        if not match:
            raise ValueError("Expected a Taiwan stock code or .TW/.TWO symbol")
        code, suffix = match.groups()
        rows, retrieved = self.client.query("TaiwanStockInfo")
        candidates = [r for r in rows if r.get("stock_id") == code]
        if not candidates:
            raise NoMarketDataError(symbol, code, "unknown Taiwan stock code")
        try:
            latest = max(day(r["date"]) for r in candidates)
            choices = [r for r in candidates if day(r["date"]) == latest]
        except (KeyError, TypeError, ValueError):
            raise VendorUnavailableError("Invalid TaiwanStockInfo metadata date") from None
        if len({r.get("type") for r in choices}) != 1:
            raise VendorUnavailableError("Conflicting market metadata")
        row = choices[0]
        exchange = row.get("type")
        if exchange not in {"twse", "tpex"}:
            raise NoMarketDataError(symbol, code, "only listed and OTC stocks supported")
        expected = ".TW" if exchange == "twse" else ".TWO"
        if suffix and suffix != expected:
            raise ValueError("Symbol suffix conflicts with the current market metadata")
        return Instrument(code, exchange, row.get("stock_name", ""), code + expected,
                          latest.isoformat(), retrieved, raw_metadata=row)

    def fetch(self, dataset: str, symbol: str, start_date: str, as_of: str) -> DataResult:
        instrument = self.resolve_symbol(symbol)
        return self._fetch(dataset, instrument, start_date, as_of)

    def _fetch(self, dataset, instrument, start_date, as_of):
        if dataset not in DATASETS:
            raise ValueError("Unsupported Taiwan dataset")
        if day(start_date) > day(as_of):
            raise ValueError("start_date must be on or before as_of")
        query = {"data_id": instrument.stock_id, "start_date": start_date, "end_date": as_of}
        rows, retrieved = self.client.query(dataset, **query)
        result = DataResult(dataset, instrument.stock_id, as_of, query, retrieved, raw_rows=rows)
        seen = set()
        for row in rows:
            try:
                if row.get("stock_id") != instrument.stock_id:
                    raise ValueError("stock_id mismatch")
                raw_date = row["date"]
                observed = day(raw_date)
                if not day(start_date) <= observed <= day(as_of):
                    continue
                # FinMind daily rows have no verified publication timestamp. Admit only
                # prior-day observations; never mislabel the observation date as publication.
                published = (self.publication_resolver(dataset, row)
                             if self.publication_resolver else None)
                basis = "injected_verified_publication_archive" if published else "unknown"
                if dataset in {REVENUE, FINANCIAL}:
                    if not published:
                        result.warnings.append(f"{raw_date}: publication date unknown; excluded")
                        continue
                    if day(published) < observed:
                        raise ValueError("publication precedes data date")
                    if day(published) > day(as_of):
                        result.warnings.append(f"{raw_date}: published after as_of; excluded")
                        continue
                else:
                    if published:
                        if day(published) < observed:
                            raise ValueError("publication precedes data date")
                        if day(published) > day(as_of):
                            continue
                    elif observed >= day(as_of):
                        continue
                    else:
                        basis = "prior_day_daily_observation; actual_publication_unknown"
                period, values, units = self._normalize(dataset, row)
                key = (period, row.get("type"), row.get("name"))
                if key in seen:
                    raise ValueError("duplicate observation; vintage ambiguous")
                seen.add(key)
                result.records.append({
                    "dataset": dataset, "stock_id": instrument.stock_id,
                    "data_date": raw_date, "period": period, "published_at": published,
                    "publication_basis": basis, "retrieved_at": retrieved,
                    "source": f"FinMind:{dataset}", "raw": row, "values": values, "units": units,
                })
            except (KeyError, TypeError, ValueError, OverflowError):
                # Any malformed/duplicate row makes the dataset unavailable. Calculations
                # must not quietly skip bad bars or select an arbitrary financial vintage.
                result.records.clear()
                result.warnings.append("Invalid or duplicate row; dataset excluded")
                break
        result.records.sort(key=lambda r: (r["period"], r["raw"].get("type", ""),
                                           r["raw"].get("name", "")))
        if not result.records:
            result.warnings.append("NO_DATA_AVAILABLE: no eligible observations; do not fabricate")
        return result

    @staticmethod
    def _normalize(dataset, row):
        if dataset == PRICE:
            values = {name: number(row[key]) for name, key in (
                ("open", "open"), ("high", "max"), ("low", "min"),
                ("close", "close"), ("volume", "Trading_Volume"))}
            if not (0 < values["low"] <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"]) <= values["high"]):
                raise ValueError("Invalid OHLC")
            if values["volume"] < 0 or not values["volume"].is_integer():
                raise ValueError("Invalid share volume")
            return row["date"], values, {"price": "TWD unadjusted", "volume": "shares"}
        if dataset == REVENUE:
            period = date(int(row["revenue_year"]), int(row["revenue_month"]), 1)
            if period > day(row["date"]):
                raise ValueError("Revenue period follows data date")
            return period.strftime("%Y-%m"), {"revenue": number(row["revenue"])}, {"revenue": "TWD"}
        if dataset == FINANCIAL:
            return row["date"], {row["type"]: number(row["value"])}, {"EPS": "TWD/share", "amount": "TWD"}
        values = {"name": row["name"], "buy": number(row["buy"]), "sell": number(row["sell"])}
        if any(values[k] < 0 or not values[k].is_integer() for k in ("buy", "sell")):
            raise ValueError("Invalid institutional share count")
        values["net"] = values["buy"] - values["sell"]
        return row["date"], values, {"buy": "shares", "sell": "shares", "net": "shares"}

    def snapshot(self, symbol: str, start_date: str, as_of: str) -> dict:
        from .calculations import (
            financial_metrics,
            institutional_metrics,
            revenue_metrics,
            technical,
        )

        if day(start_date) > day(as_of):
            raise ValueError("start_date must be on or before as_of")
        instrument = self.resolve_symbol(symbol)
        datasets = {}
        for dataset in DATASETS:
            try:
                datasets[dataset] = self._fetch(dataset, instrument, start_date, as_of).to_dict()
            except VendorUnavailableError as exc:
                datasets[dataset] = {"status": "unavailable", "records": [], "raw_rows": [],
                                     "warnings": [str(exc)], "dataset": dataset,
                                     "stock_id": instrument.stock_id, "as_of": as_of,
                                     "retrieved_at": self.client.now()}
        prices = datasets[PRICE]["records"]
        return {
            "schema_version": 1, "instrument": asdict(instrument), "as_of": as_of,
            "analysis_semantics": "Asia/Taipei end-of-day; daily data available next day",
            "datasets": datasets,
            "technical": technical(prices),
            "monthly_revenue": revenue_metrics(datasets[REVENUE]["records"]),
            "quarterly_financials": financial_metrics(datasets[FINANCIAL]["records"]),
            "institutions": institutional_metrics(datasets[INSTITUTIONS]["records"], prices,
                                                  instrument.exchange),
            "limitations": ["Current identity is not historical listing membership",
                            "Unverified financial publication dates are excluded",
                            "No vintage archive; revised data is not certified point-in-time",
                            "No LLM analysis, broker orders, or portfolio simulation"],
        }
