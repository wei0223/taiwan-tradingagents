"""Parse the explicit current-quarter column of an archived official MOPS income table."""

from html.parser import HTMLParser

from .provider import number


class Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        if tag in ["td", "th"]:
            self.cell = ""

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ["td", "th"] and self.cell is not None:
            if self.row is not None:
                self.row.append(" ".join(self.cell.split()))
            self.cell = None
        if tag == "tr" and self.row:
            self.rows.append(self.row)
            self.row = None


def parse_income(content, year, quarter):
    text = content.decode("utf-8-sig", errors="strict")
    parser = Rows()
    parser.feed(text)
    if "合併綜合損益表" not in text or "新台幣仟元" not in text:
        raise ValueError("Consolidated statement/unit not verified")
    heading = next((r for r in parser.rows if r[0] == "會計項目"), None)
    expected = f"{year - 1911}年第{quarter}季"
    # Annual first columns are cumulative; never difference annual EPS.
    if quarter == 4:
        return {"status": "annual_ytd_not_quarter_eps", "rows": parser.rows, "eps_diluted": None}
    allowed = {expected}
    if quarter == 1:
        allowed.add(f"{year - 1911}年01月01日至{year - 1911}年03月31日")
    if not heading or len(heading) < 2 or heading[1] not in allowed:
        raise ValueError("Explicit current quarter column not verified")
    labels = {
        "revenue_twd": "營業收入合計",
        "operating_profit_twd": "營業利益（損失）",
        "pretax_profit_twd": "稅前淨利（淨損）",
        "net_profit_twd": "本期淨利（淨損）",
        "parent_profit_twd": "母公司業主（淨利∕損）",
        "minority_profit_twd": "非控制權益（淨利∕損）",
        "eps_basic": "基本每股盈餘",
        "eps_diluted": "稀釋每股盈餘",
    }
    values = {}
    for key, label in labels.items():
        cells = [r[1] for r in parser.rows if r[0] == label and len(r) > 1 and r[1].strip()]
        if len(cells) != 1:
            values[key] = None
            continue
        raw = cells[0].replace(",", "").replace("(", "-").replace(")", "")
        values[key] = number(raw) * (1 if key.startswith("eps_") else 1000)
    return {
        "status": "single_quarter",
        "period": f"{year}-Q{quarter}",
        "scope": "consolidated",
        "column_header": heading[1],
        "values": values,
        "rows": parser.rows,
        "weighted_diluted_shares": None,
        "limitation": "EPS note share denominator not inferred from rounded EPS",
    }
