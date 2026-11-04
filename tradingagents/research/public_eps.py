"""Public publisher attribution of FactSet annual EPS; no FactSet API or implied license."""

import base64
import hashlib
import html
import re
from decimal import Decimal
from html.parser import HTMLParser
from urllib.parse import urlparse

from .review import _end_of_day
from .scenarios import timestamp

PUBLISHERS = {"news.cnyes.com", "m.cnyes.com", "gfemobile.cnyes.com"}


class Article(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.skip = 0
        self.h1 = False
        self.title = ""
        self.text = ""
        self.publications = []
        self.tables = []
        self.table = None
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        attrs = {k.lower(): v for k, v in attrs}
        if tag == "meta" and attrs.get("property") == "article:published_time":
            self.publications.append(attrs["content"])
        if tag == "article":
            self.active = True
        if not self.active:
            return
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "h1":
            self.h1 = True
        if tag == "time" and attrs.get("datetime"):
            self.publications.append(attrs["datetime"])
        if tag == "table":
            self.table = {"context": self.text[-200:], "rows": []}
        if tag == "tr" and self.table is not None:
            self.row = []
        if tag in ("td", "th") and self.row is not None:
            self.cell = ""

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1
        if tag == "h1":
            self.h1 = False
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell.strip())
            self.cell = None
        if tag == "tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row = None
        if tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None
        if tag in ("p", "h1", "td", "th"):
            self.text += " "
        if tag == "article":
            self.active = False

    def handle_data(self, value):
        if not self.active or self.skip:
            return
        self.text += value
        if self.h1:
            self.title += value
        if self.cell is not None:
            self.cell += value


def parse(doc, stock):
    parsed_url = urlparse(doc["source_url"])
    if (
        parsed_url.scheme != "https"
        or parsed_url.hostname not in PUBLISHERS
        or not re.fullmatch(r"/news/id/\d+/?", parsed_url.path)
        or parsed_url.query
        or parsed_url.username
        or parsed_url.password
    ):
        raise ValueError("Supported public article URL required")
    raw = base64.b64decode(doc["content_base64"], validate=True)
    if hashlib.sha256(raw).hexdigest() != doc["sha256"]:
        raise ValueError("Public EPS original hash mismatch")
    article = Article()
    article.feed(raw.decode("utf-8-sig"))
    if (
        not re.search(r"FactSet", article.title, re.I)
        or not re.search(r"[（(]" + re.escape(stock) + r"-TW[）)]", article.title)
        or "EPS" not in article.title.upper()
    ):
        raise ValueError("Article title is not this Taiwan issuer's FactSet EPS")
    publications = {timestamp(t) for t in article.publications}
    if len(publications) != 1:
        raise ValueError("Unambiguous article publication timestamp required")
    published = publications.pop()
    if timestamp(doc["captured_at"]) < published:
        raise ValueError("Capture before article publication")
    tables = [t for t in article.tables if re.search(r"市場預估\s*EPS\s*$", t["context"], re.I)]
    if len(tables) != 1:
        raise ValueError("One explicitly headed EPS table required")
    rows = tables[0]["rows"]
    if not rows or rows[0][0].strip() != "預估值":
        raise ValueError("Annual EPS table header required")
    headers = rows[0][1:]
    years = []
    for h in headers:
        m = re.fullmatch(r"(20\d{2})年(?:[（(]前值[）)])?", re.sub(r"\s", "", h))
        if not m:
            raise ValueError("Explicit annual EPS periods required; no NTM inference")
        years.append(int(m[1]))
    if len(years) != len(set(years)):
        raise ValueError("Duplicate EPS fiscal years")
    medians = [r for r in rows[1:] if r and r[0].strip() == "中位數"]
    if len(medians) != 1 or len(medians[0]) != len(years) + 1:
        raise ValueError("One aligned median EPS row required")
    count = re.search(r"共\s*(\d+)\s*位分析師.*?做出\s*(20\d{2})\s*年EPS預估", article.text, re.S)
    estimates = []
    for year, cell in zip(years, medians[0][1:], strict=True):
        cleaned = re.sub(r"\s", "", cell)
        if cleaned in ("--", "—", "-", "N/A", ""):
            continue
        value = re.fullmatch(
            r"(-?\d[\d,]*(?:\.\d+)?)(?:[（(](-?\d[\d,]*(?:\.\d+)?)[）)])?", cleaned
        )
        if not value:
            raise ValueError("Unsupported EPS cell; no guessing")
        number = Decimal(value[1].replace(",", ""))
        estimates.append(
            {
                "fiscal_year": year,
                "period_kind": "annual",
                "statistic": "median",
                "eps": str(number),
                "reported_cell": cell,
                "prior_eps": str(Decimal(value[2].replace(",", ""))) if value[2] else None,
                "analyst_count": int(count[1]) if count and int(count[2]) == year else None,
                "currency": None,
                "reported_unit": "元/股",
                "eps_basis": "unknown",
                "consensus_as_of": None,
                "published_at": published.isoformat(),
                "source_url": doc["source_url"],
                "source_sha256": doc["sha256"],
                "captured_at": doc["captured_at"],
                "source_title": article.title,
                "table_header": headers,
                "median_row": medians[0],
                "attribution": "鉅亨轉引 FactSet",
                "source_kind": "public_media_attribution",
            }
        )
    return estimates


def build(packet, stock, as_of):
    if packet and (packet.get("stock_id") != stock or packet.get("as_of") != as_of):
        raise ValueError("Public consensus company/cutoff mismatch")
    sources = (packet or {}).get("sources", {})
    all_estimates, excluded = [], []
    for key, doc in sources.items():
        estimates = parse(doc, stock)
        if any(
            max(timestamp(e["published_at"]), timestamp(e["captured_at"])) > _end_of_day(as_of)
            for e in estimates
        ):
            excluded.append({"source_id": key, "reason": "after_research_cutoff"})
            continue
        all_estimates.extend({**e, "source_id": key} for e in estimates)
    selected = []
    for year in sorted({e["fiscal_year"] for e in all_estimates}):
        group = [e for e in all_estimates if e["fiscal_year"] == year]
        latest = max(timestamp(e["published_at"]) for e in group)
        candidates = [e for e in group if timestamp(e["published_at"]) == latest]
        if len({e["eps"] for e in candidates}) != 1:
            raise ValueError("Conflicting same-time EPS sources require review")
        selected.append(sorted(candidates, key=lambda e: e["source_id"])[0])
    return {
        "schema_version": 1,
        "stock_id": stock,
        "as_of": as_of,
        "status": "available_public_mentions" if selected else "unavailable",
        "provider": "FactSet via public media",
        "estimates": selected,
        "vintages": all_estimates,
        "excluded_sources": excluded,
        "selection_policy": "latest_publication_per_year_among_supplied_articles",
        "exhaustive_coverage_verified": False,
        "factset_direct_access": False,
        "statistic": "median",
        "annual_not_ntm": True,
        "limitation": "報導日期不等於FactSet更新日；基本/稀釋及調整口徑、幣別未獨立確認；不自動接入估值或績效。",
    }


def display_rows(package):
    return [
        [
            str(e["fiscal_year"]),
            e["eps"],
            e["analyst_count"] if e["analyst_count"] is not None else "未提供",
            e["published_at"][:10],
            "鉅亨轉引 FactSet",
        ]
        for e in package["estimates"]
    ]


def section(package, table):
    out = '<section id="consensus-eps"><h2>FactSet 共識預估 EPS（公開轉引）</h2>'
    if not package["estimates"]:
        return (
            out + "<p>未取得可核對的公開年度EPS中位數；保留不可用，不以模型試算補位。</p></section>"
        )
    out += table(
        ["財務年度", "EPS中位數（報導元/股）", "分析師人數", "報導日期", "來源"],
        display_rows(package),
    )
    out += (
        "<p>"
        + html.escape(package["limitation"])
        + " 僅代表已取得報導，不能保證為FactSet最新完整資料。各年度人數未提供時不沿用其他年度。</p>"
    )
    for e in package["estimates"]:
        out += (
            '<p><a href="'
            + html.escape(e["source_url"], quote=True)
            + '">'
            + html.escape(e["source_title"])
            + "</a></p>"
        )
    return out + '<p><a href="public-eps.json">原始轉引與選版紀錄</a></p></section>'
