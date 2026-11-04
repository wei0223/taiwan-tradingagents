"""Candles and fixed indicator panels drawn from the calculation package only."""

import html


def scene(package, weekly=False):
    if package.get("status") != "ready":
        return None
    source = package.get("weekly", {}).get("complete") if weekly else package["price_series"]["raw"]
    if not source:
        return None
    window = 52 if weekly else 120
    dates = source["dates"][-window:]
    bars = source["bars"][-window:]
    series = source["indicators"]
    n = len(bars)
    if not n:
        return None
    commands = []

    def text(x, y, value):
        commands.append(("text", x, y, str(value)))

    def line(x1, y1, x2, y2, color):
        commands.append(("line", x1, y1, x2, y2, color))

    def poly(points, color):
        commands.append(("polyline", points, color))

    def rect(x, y, w, h, color):
        commands.append(("rect", x, y, w, h, color))

    text(
        50, 22, ("完整週K" if weekly else "日K") + " · 原始OHLC · " + dates[0] + " 至 " + dates[-1]
    )

    def x(i):
        return 65 + i * 875 / max(n - 1, 1)

    low = min(b["low"] for b in bars)
    high = max(b["high"] for b in bars)
    span = high - low or 1

    def y(p):
        return 280 - (p - low) / span * 220

    text(50, 45, f"價格（元） {low:,.2f}～{high:,.2f}")
    for fraction in (0, 0.5, 1):
        level = low + span * fraction
        line(55, y(level), 945, y(level), "#edf1f4")
        text(948, y(level) + 4, f"{level:,.0f}")
    colors = []
    for i, b in enumerate(bars):
        color = "#be554a" if b["close"] >= b["open"] else "#268578"
        colors.append(color)
        line(x(i), y(b["high"]), x(i), y(b["low"]), color)
        rect(
            x(i) - 2.4,
            min(y(b["open"]), y(b["close"])),
            4.8,
            max(0.8, abs(y(b["close"]) - y(b["open"]))),
            color,
        )
    for index, (key, color) in enumerate(
        [
            (("SMA_10" if weekly else "SMA_20"), "#d99221"),
            (("SMA_20" if weekly else "SMA_60"), "#635ca8"),
            (("SMA_40" if weekly else "SMA_240"), "#286b9a"),
        ]
    ):
        line(440 + index * 160, 42, 465 + index * 160, 42, color)
        text(470 + index * 160, 46, key)
        points = [
            (x(i), y(v))
            for i, v in enumerate(series.get(key, [])[-window:])
            if v is not None and low <= v <= high
        ]
        if len(points) > 1:
            poly(points, color)
    text(50, 310, "成交量（股）")
    maximum = max(b["volume"] for b in bars) or 1
    text(650, 310, f"區間最高量 {maximum:,.0f} 股")
    for i, b in enumerate(bars):
        rect(
            x(i) - 2.4,
            390 - b["volume"] / maximum * 65,
            4.8,
            max(0.5, b["volume"] / maximum * 65),
            colors[i],
        )
    text(50, 420, "RSI14（Wilder）")
    rsi = series.get("RSI_14", [])[-window:]
    for level in (30, 70):
        line(55, 510 - level * 0.75, 945, 510 - level * 0.75, "#ced8df")
        text(950, 514 - level * 0.75, level)
    points = [(x(i), 510 - v * 0.75) for i, v in enumerate(rsi) if v is not None]
    if len(points) > 1:
        poly(points, "#635ca8")
    text(50, 545, "MACD（12,26,9）；柱 = MACD - signal")
    histogram = series.get("MACD.macdhist", [])[-window:]
    macd = series.get("MACD.macd", [])[-window:]
    signal = series.get("MACD.macdsignal", [])[-window:]
    maximum = (
        max([abs(v) for data in (histogram, macd, signal) for v in data if v is not None] or [1])
        or 1
    )

    def yy(v):
        return 610 - v / maximum * 45

    line(55, 610, 945, 610, "#ced8df")
    for i, v in enumerate(histogram):
        if v is not None:
            rect(
                x(i) - 2.4,
                min(yy(v), 610),
                4.8,
                max(0.5, abs(yy(v) - 610)),
                "#be554a" if v >= 0 else "#268578",
            )
    for data, color in [(macd, "#d99221"), (signal, "#635ca8")]:
        points = [(x(i), yy(v)) for i, v in enumerate(data) if v is not None]
        if len(points) > 1:
            poly(points, color)
    text(50, 684, "紅：收≥開／正柱；綠：收<開／負柱。均線超出當前價格範圍時不畫；完整值見JSON。")
    return {"width": 1000, "height": 700, "commands": commands}


def svg(package, weekly=False):
    data = scene(package, weekly)
    if data is None:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 80"><text x="15" y="40">技術資料未通過固定歷史驗收，停止繪圖。</text></svg>'
    out = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 700" role="img"><title>固定程式產生K線與指標圖</title><rect width="1000" height="700" fill="white"/>'
    for c in data["commands"]:
        if c[0] == "text":
            out += f'<text x="{c[1]:.2f}" y="{c[2]:.2f}" font-size="14">{html.escape(c[3])}</text>'
        elif c[0] == "line":
            out += f'<line x1="{c[1]:.2f}" y1="{c[2]:.2f}" x2="{c[3]:.2f}" y2="{c[4]:.2f}" stroke="{c[5]}"/>'
        elif c[0] == "rect":
            out += f'<rect x="{c[1]:.2f}" y="{c[2]:.2f}" width="{c[3]:.2f}" height="{c[4]:.2f}" fill="{c[5]}"/>'
        elif c[0] == "polyline":
            out += (
                '<polyline points="'
                + " ".join(f"{x:.2f},{y:.2f}" for x, y in c[1])
                + f'" fill="none" stroke="{c[2]}" stroke-width="1.5"/>'
            )
    return out + "</svg>"


def drawing(package, font="ReaderCJK", weekly=False):
    from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
    from reportlab.lib import colors

    data = scene(package, weekly)
    if data is None:
        return None
    d = Drawing(500, 350)
    for c in data["commands"]:
        if c[0] == "text":
            d.add(String(c[1] / 2, (700 - c[2]) / 2, c[3], fontName=font, fontSize=7))
        elif c[0] == "line":
            d.add(
                Line(
                    c[1] / 2,
                    (700 - c[2]) / 2,
                    c[3] / 2,
                    (700 - c[4]) / 2,
                    strokeColor=colors.HexColor(c[5]),
                    strokeWidth=0.5,
                )
            )
        elif c[0] == "rect":
            d.add(
                Rect(
                    c[1] / 2,
                    (700 - c[2] - c[4]) / 2,
                    c[3] / 2,
                    c[4] / 2,
                    fillColor=colors.HexColor(c[5]),
                    strokeColor=None,
                )
            )
        elif c[0] == "polyline":
            d.add(
                PolyLine(
                    [v for x, y in c[1] for v in (x / 2, (700 - y) / 2)],
                    strokeColor=colors.HexColor(c[2]),
                    strokeWidth=0.7,
                )
            )
    return d


def section(package, state, table):
    import json

    out = (
        '<section id="fixed-technical"><h2>固定程式技術分析</h2><p>引擎 '
        + html.escape(package["engine_version"])
        + "；至少1000個官方交易日。公司資料狀態："
        + html.escape(state["company_status"])
        + "。</p>"
    )
    if package["status"] != "ready":
        return (
            out
            + "<p>歷史或依賴驗收未通過，停止指標與K線；缺口見technical-package.json。沒有使用舊公式或LLM補算。</p></section>"
        )
    raw = package["price_series"]["raw"]
    out += '<figure><img src="candles.svg" alt="固定OHLC日K及均線、成交量、RSI、MACD" style="width:100%"><figcaption>最近120日原始價K線；所有圖形直接來自同一份計算包。</figcaption></figure>'
    out += '<figure><img src="weekly-candles.svg" alt="排除不完整週的週K" style="width:100%"><figcaption>完整週K，排除本週及首週不完整資料。</figcaption></figure>'
    out += (
        "<details><summary>完整指標最新值、固定參數及暖機期</summary>"
        + table(
            ["指標", "最新值", "參數／定義", "暖機棒數"],
            [
                [
                    key,
                    "不可用（暖機或分母未定義）" if value is None else f"{value:,.6f}",
                    json.dumps(
                        raw["definitions"][key].get(
                            "parameters", raw["definitions"][key].get("definition", "")
                        ),
                        ensure_ascii=False,
                    ),
                    raw["definitions"][key]["warmup_bars"],
                ]
                for key, value in raw["latest"].items()
            ],
        )
        + "</details>"
    )
    detected = [name for name, p in raw["patterns"].items() if p["values"][-1] not in (None, 0)]
    out += (
        "<p>已計算 "
        + str(raw["indicator_columns"])
        + " 個指標欄位及 "
        + str(raw["pattern_functions"])
        + " 個K線型態函式。末日符合型態："
        + html.escape("、".join(detected) if detected else "無")
        + "；型態分數不是勝率，也不是交易指令。</p>"
    )
    return (
        out
        + '<p><a href="technical-package.json">1000日完整計算序列、原始價／還原價、定義與事件</a> · <a href="company-plan.json">固定公司查閱清單</a></p></section>'
    )
