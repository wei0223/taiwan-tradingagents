"""Bounded, mockable official-source families; ezSearch is cross-check only."""

import base64
import hashlib
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

from tradingagents.research.bundle import fingerprint

from .filing_links import candidates, verify_chain
from .filings import ENDPOINT, HOST, ListingParser
from .originals import compact, html_data, review_original, source_restricted

MOPS = "https://mopsov.twse.com.tw/mops/web/"
FAMILIES = (
    "financial_reports_notes",
    "monthly_revenue",
    "capital_registration",
    "fundraising",
    "corrections",
)


class OfficialSources:
    def __init__(
        self, store, transport=None, clock=None, max_requests=90, max_pdfs=24, min_interval=None
    ):
        self.min_interval = (
            (1.5 if transport is None else 0) if min_interval is None else min_interval
        )
        self.last_request = 0
        if (
            not 1 <= max_requests <= 200
            or not 1 <= max_pdfs <= 40
            or not 0 <= self.min_interval <= 60
        ):
            raise ValueError("Source budgets or interval outside supported bounds")
        if transport is None:
            import requests

            transport = requests.Session()
        self.store, self.transport = store, transport
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_requests, self.max_pdfs = max_requests, max_pdfs
        self.requests = self.pdfs = 0
        self.inherited_refusals = self.store.recent_refusals(self.clock())
        self.refused = set(self.inherited_refusals)
        self.cache = {}
        self.receipt_log = []

    def request(self, method, url, query=None):
        host = urlsplit(url).netloc
        if urlsplit(url).scheme != "https" or host not in {"doc.twse.com.tw", "mopsov.twse.com.tw"}:
            raise ValueError("Official HTTPS source required")
        key = fingerprint([method, url, query])
        if key in self.cache:
            return self.cache[key]
        receipt = {
            "source_url": url,
            "query": query,
            "published_at": None,
            "publication_basis": "unknown_current_version_known_from_public_capture_only",
        }
        if host in self.refused or self.requests >= self.max_requests:
            receipt.update(
                status="skipped_after_source_refusal_or_budget", public_capture_verified=False
            )
        else:
            time.sleep(max(0, self.min_interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            self.requests += 1
            try:
                options = {"timeout": 35, "allow_redirects": False}
                if query is not None:
                    options["data" if method == "post" else "params"] = query
                response = getattr(self.transport, method)(url, **options)
                raw = response.content
                receipt.update(
                    http_status=response.status_code,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    content_base64=base64.b64encode(raw).decode(),
                    public_capture_verified=response.status_code == 200,
                )
                if response.status_code in {403, 429} or source_restricted(raw):
                    self.refused.add(host)
                    receipt.update(status="source_rejected", public_capture_verified=False)
            except Exception as error:
                receipt.update(
                    status="unavailable",
                    public_capture_verified=False,
                    error_kind=type(error).__name__,
                )
        receipt["captured_at"] = self.clock().isoformat()
        receipt_id = self.store.capture(receipt)
        self.receipt_log.append(receipt_id)
        self.cache[key] = (receipt, receipt_id)
        return receipt, receipt_id

    @staticmethod
    def content(receipt):
        if receipt.get("http_status") != 200 or receipt.get("public_capture_verified") is not True:
            raise ValueError("Source unavailable")
        raw = base64.b64decode(receipt["content_base64"], validate=True)
        if hashlib.sha256(raw).hexdigest() != receipt["sha256"]:
            raise ValueError("Source changed")
        return raw

    @classmethod
    def issuer(cls, receipt, company):
        parser, _ = html_data(cls.content(receipt))
        text = compact(" ".join(parser.parts))
        name = re.escape(compact(company["name"]))
        if not re.search(
            r"本資料由(?:\([^)]*\))?(?:" + company["stock_id"] + ")?" + name + "公司提供", text
        ):
            raise ValueError("Official issuer heading unavailable")
        return parser, text

    def document(self, row, listing, candidate):
        if self.pdfs >= self.max_pdfs:
            raise ValueError("PDF capture budget exhausted")
        self.pdfs += 1
        query = {
            "step": "9",
            "kind": candidate["kind"],
            "co_id": candidate["stock_id"],
            "filename": candidate["filename"],
            "colorchg": "1",
            "DEBUG": "",
        }
        response, form_id = self.request("post", ENDPOINT, query)
        raw = self.content(response)
        _, encoding = html_data(raw)
        parser = ListingParser()
        parser.feed(raw.decode(encoding))
        paths = [p for p in parser.links if re.fullmatch(r"/pdf/[A-Za-z0-9_]+\.pdf", p)]
        if len(paths) != 1 or not paths[0].startswith("/pdf/" + candidate["filename"][:-4] + "_"):
            raise ValueError("Official PDF target unavailable")
        pdf, pdf_id = self.request("get", HOST + paths[0])
        proof = verify_chain(
            row,
            listing,
            {"filename": candidate["filename"], "download_response": response, "pdf_response": pdf},
        )
        return proof, [form_id, pdf_id]

    def collect(self, company, plan):
        stock = company["stock_id"]
        if not re.fullmatch(r"\d{4,6}", stock) or company["exchange"] not in {"twse", "tpex"}:
            raise ValueError("Explicit valid company/exchange required")
        resources = []

        def resource(key, family, operation, baseline=()):
            receipts = list(baseline)
            before = len(self.receipt_log)
            try:
                data, receipts, content_sha = operation()
                result = {
                    "key": key,
                    "family": family,
                    "status": "verified_current",
                    "data": data,
                    "content_sha256": content_sha,
                }
            except (ValueError, KeyError, TypeError, UnicodeError) as error:
                receipts = list(baseline) + self.receipt_log[before:]
                result = {
                    "key": key,
                    "family": family,
                    "status": "pending",
                    "reason": type(error).__name__
                    + ": source/identity/period/budget requires review",
                }
            result["receipt_ids"] = receipts
            resources.append(result)

        def direct(operation, url, query=None, method="post"):
            receipt, receipt_id = self.request(method, url, query)
            data = operation(receipt)
            return data, [receipt_id], receipt["sha256"]

        # Current issued capital and registry change index are distinct from EPS weights.
        def basic(receipt):
            _, text = self.issuer(receipt, company)
            match = re.search(r"已發行普通股數或TDR原股發行股數([\d,]+)股", text)
            if not match or f"股票代號{stock}" not in text:
                raise ValueError("Issued common share field unavailable")
            return {
                "issued_common_shares": int(match[1].replace(",", "")),
                "basis": "current_issued_not_weighted_or_float",
                "effective_issue_date": None,
                "numeric_fields_programmatically_parsed": True,
            }

        query = {"step": "1", "firstin": "1", "off": "1", "TYPEK": "all", "co_id": stock}
        resource(
            "capital:basic",
            "capital_registration",
            lambda: direct(basic, MOPS + "ajax_t05st03", query),
        )

        def registration(receipt):
            parser, text = self.issuer(receipt, company)
            if "變更公司執照時間" not in text:
                raise ValueError("Capital change index unavailable")
            dates = sorted(
                {
                    compact(c[0])
                    for c in parser.rows
                    if c and re.fullmatch(r"\d{2,3}年\d{1,2}月", compact(c[0]))
                }
            )
            if not dates:
                raise ValueError("Empty capital listing not evidence of no changes")
            return {
                "observed_registration_months": dates,
                "all_issue_completion_events_covered": False,
            }

        resource(
            "capital:registration_index",
            "capital_registration",
            lambda: direct(registration, MOPS + "ajax_t05st05", query),
        )

        for period in plan["monthly_periods"]:
            year, month = map(int, period.split("-"))
            params = {
                "step": "1",
                "firstin": "1",
                "off": "1",
                "TYPEK": "all",
                "isnew": "false",
                "co_id": stock,
                "year": str(year - 1911),
                "month": f"{month:02d}",
            }
            url = MOPS + "ajax_t05st10_ifrs?" + urlencode(params)
            row = {
                "COMPANY_ID": stock,
                "COMPANY_NAME": company["name"],
                "AN_CODE": "F22",
                "SUBJECT": f"{year - 1911}年{month}月營業收入",
                "HYPERLINK": url,
            }

            def monthly(receipt, row=row):
                result = review_original(row, receipt)
                if not result["content_verified"]:
                    raise ValueError("Monthly original not verified")
                return {
                    "observations": result["numeric_observations"],
                    "initial_publication_proven": False,
                }

            resource(
                "revenue:" + period,
                "monthly_revenue",
                lambda url=url, operation=monthly: direct(operation, url, method="get"),
            )

        for year in sorted({int(p[:4]) for p in plan["financial_periods"]}):
            url = (
                ENDPOINT
                + "?"
                + urlencode(
                    {
                        "step": "1",
                        "colorchg": "1",
                        "co_id": stock,
                        "year": str(year - 1911),
                        "seamon": "",
                        "mtype": "A",
                    }
                )
            )
            listing, listing_id = self.request("get", url)
            for period in [p for p in plan["financial_periods"] if int(p[:4]) == year]:
                quarter = int(period[-1])
                row = {
                    "COMPANY_ID": stock,
                    "COMPANY_NAME": company["name"],
                    "AN_CODE": "E02",
                    "SUBJECT": f"{year - 1911}年第{quarter}季財務報告書",
                    "HYPERLINK": url,
                    "origin": "direct_document_requirement_not_an_announcement_event",
                }
                try:
                    choices = candidates(row, listing)
                    resources.append(
                        {
                            "key": "filing-list:" + period,
                            "family": "financial_reports_notes",
                            "status": "verified_current",
                            "data": {
                                "candidates": choices,
                                "scope": "observed_current_period_listing_not_historical_versions",
                            },
                            "receipt_ids": [listing_id],
                            "content_sha256": listing["sha256"],
                        }
                    )
                except (ValueError, KeyError, TypeError, UnicodeError):
                    choices = []
                for edition in plan.get("financial_editions", ["AI1", "AIA"]):
                    selected = [
                        d
                        for d in choices
                        if d["filename"] == f"{year}0{quarter}_{stock}_{edition}.pdf"
                    ]

                    def download(
                        selected=selected, row=row, listing=listing, listing_id=listing_id
                    ):
                        if len(selected) != 1:
                            raise ValueError("Required financial edition missing")
                        proof, refs = self.document(row, listing, selected[0])
                        return proof, [listing_id, *refs], proof["pdf_sha256"]

                    resource(
                        "filing:" + period + ":" + edition,
                        "financial_reports_notes",
                        download,
                        [listing_id],
                    )

        # Prospectus query is a source family, not proof that an offering completed.
        year = plan["fundraising_year"]
        url = (
            ENDPOINT
            + "?"
            + urlencode(
                {
                    "step": "1",
                    "colorchg": "1",
                    "co_id": stock,
                    "year": str(year - 1911),
                    "seamon": "",
                    "mtype": "B",
                }
            )
        )
        listing, listing_id = self.request("get", url)
        found = []
        for month in range(1, 13):
            row = {
                "COMPANY_ID": stock,
                "COMPANY_NAME": company["name"],
                "AN_CODE": "E01",
                "SUBJECT": f"{year - 1911}年{month}月公開說明書",
                "HYPERLINK": url,
                "origin": "direct_document_requirement_not_an_announcement_event",
            }
            try:
                found.extend((row, candidate) for candidate in candidates(row, listing))
            except (ValueError, KeyError, TypeError, UnicodeError):
                resources.append(
                    {
                        "key": "fundraising:listing",
                        "family": "fundraising",
                        "status": "pending",
                        "receipt_ids": [listing_id],
                        "reason": "Prospectus listing unavailable",
                    }
                )
                break
        else:
            parser, _ = html_data(self.content(listing))
            all_named = set(
                re.findall(
                    rf"{year}\d{{2}}_{stock}_[A-Za-z0-9]+\.(?:pdf|zip)", " ".join(parser.parts)
                )
            )
            unhandled = sorted(all_named - {c["filename"] for _, c in found})
            resources.append(
                {
                    "key": "fundraising:listing",
                    "family": "fundraising",
                    "status": "pending" if unhandled else "verified_current",
                    "receipt_ids": [listing_id],
                    "content_sha256": listing["sha256"],
                    "data": {
                        "observed_files": [c["filename"] for _, c in found],
                        "unhandled_files": unhandled,
                        "offering_completion_proven": False,
                        "no_events_ever_proven": False,
                    },
                }
            )
        for row, candidate in found:

            def prospectus(row=row, candidate=candidate):
                proof, refs = self.document(row, listing, candidate)
                return proof, [listing_id, *refs], proof["pdf_sha256"]

            resource("prospectus:" + candidate["filename"], "fundraising", prospectus, [listing_id])

        for period in plan["correction_periods"]:
            year, quarter = int(period[:4]), int(period[-1])
            query = {
                "step": "1",
                "firstin": "ture",
                "off": "1",
                "isQuery": "Y",
                "TYPEK": "sii" if company["exchange"] == "twse" else "otc",
                "year": str(year - 1911),
                "season": f"{quarter:02d}",
            }

            def corrections(receipt, year=year, quarter=quarter):
                parser, _ = html_data(self.content(receipt))
                text = compact(" ".join(parser.parts))
                if not all(
                    x in text
                    for x in (
                        f"資料年度:{year - 1911}",
                        f"季別:{quarter:02d}",
                        "公司代號",
                        "更(補)正內容",
                    )
                ):
                    raise ValueError("Correction query controls or table unavailable")
                rows = [c for c in parser.rows if len(c) == 6 and c[0] == stock]
                return {
                    "matching_rows": rows,
                    "state": "observed_rows" if rows else "no_rows_in_this_captured_table",
                    "historical_no_corrections_proven": False,
                }

            resource(
                "corrections:" + period,
                "corrections",
                lambda query=query, operation=corrections: direct(
                    operation, MOPS + "ajax_t56sb31_q1", query
                ),
            )
        return resources
