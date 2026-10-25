"""Archive all-category MOPS search results; never claim absence from a latest-ten list."""

import base64
import hashlib
import json
import re
from datetime import UTC, datetime

from tradingagents.dataflows.errors import VendorUnavailableError

from .publications import MopsAnnouncementClient, query_date


class MaterialEventClient:
    def __init__(self, transport=None, clock=None):
        if transport is None:
            import requests

            transport = requests.Session()
        self.transport = transport
        self.clock = clock or (lambda: datetime.now(UTC))

    def query(self, stock_id, start, end):
        if not re.fullmatch(r"\d{4,6}", stock_id) or start > end:
            raise ValueError("Invalid material event query")
        params = {
            "step": "00",
            "RADIO_CM": "2",
            "TYPEK": "",
            "CO_MARKET": "",
            "CO_ID": stock_id,
            "PRO_ITEM": "",
            "SUBJECT": "",
            "SDATE": query_date(start),
            "EDATE": query_date(end),
            "lang": "TW",
            "AN": "",
        }
        try:
            response = self.transport.post(
                MopsAnnouncementClient.endpoint, data=params, timeout=25, allow_redirects=False
            )
        except Exception as exc:
            error = VendorUnavailableError("Material announcement transport unavailable")
            error.failure_receipt = {
                "stock_id": stock_id,
                "source_url": MopsAnnouncementClient.endpoint,
                "query": params,
                "captured_at": self.clock().isoformat(),
                "public_capture_verified": False,
                "status": "unavailable",
                "error_kind": type(exc).__name__,
            }
            raise error from None
        raw = response.content
        receipt = {
            "stock_id": stock_id,
            "source_url": MopsAnnouncementClient.endpoint,
            "query": params,
            "captured_at": self.clock().isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "content_base64": base64.b64encode(raw).decode(),
            "http_status": response.status_code,
            "published_at": None,
            "public_capture_verified": response.status_code == 200,
        }
        try:
            data = json.loads(raw.decode("utf-8-sig"))
            rows = data["data"]
            if (
                response.status_code != 200
                or data.get("status") != "success"
                or not isinstance(rows, list)
                or len(rows) >= 1000
            ):
                raise ValueError("Unverified/truncated query")
            if any(str(r.get("COMPANY_ID")) != stock_id for r in rows):
                raise ValueError("Company mismatch")
        except (ValueError, KeyError, TypeError, UnicodeError):
            error = VendorUnavailableError("Material announcement coverage unavailable")
            error.failure_receipt = receipt
            raise error from None
        return {
            **receipt,
            "rows": rows,
            "status": "captured_unclassified",
            "coverage_verified": False,
            "limitation": "Classification and numeric original review required; search receipt alone does not prove completeness",
        }
