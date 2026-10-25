#!/usr/bin/env python3
"""Trade Journal.

Parses MetaTrader 5 HTML trade-history reports and builds a single-page
HTML dashboard with the key numbers: win rate, profit factor, drawdown,
best/worst hours and weekdays.

Usage:
    python trade_journal.py report.html -o dashboard.html
    python trade_journal.py --demo            # try it without a report
"""

import argparse
import json
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    """One closed trade pulled out of a report."""

    open_time: datetime
    close_time: datetime
    symbol: str
    side: str          # "buy" or "sell", "" when the report does not say
    volume: float      # lots, 0 when missing
    entry: float       # entry price, 0 when missing
    sl: float          # stop loss price, 0 when missing
    tp: float          # take profit price, 0 when missing
    profit: float      # net profit (money), negative for a loss


# ---------------------------------------------------------------------------
# HTML table scraping (stdlib only)
# ---------------------------------------------------------------------------


class _TableScraper(HTMLParser):
    """Collects every <table> as a list of rows of plain text cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._table = None
        self._row = None
        self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            text = " ".join("".join(self._cell).split())
            self._row.append(text)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


# Header names MT5 uses (case-insensitive, a few common spellings included).
COLUMN_ALIASES = {
    "time": ("time", "open time", "close time", "date"),
    "position": ("position", "ticket", "deal", "order", "#"),
    "symbol": ("symbol", "item", "instrument"),
    "type": ("type", "action"),
    "direction": ("direction",),
    "volume": ("volume", "lots", "size"),
    "price": ("price", "open price", "entry price"),
    "sl": ("s/l", "sl", "stop loss"),
    "tp": ("t/p", "tp", "take profit"),
    "profit": ("profit", "net profit", "p/l", "gross p/l", "gross profit"),
}


def _clean(text):
    """Normalize a cell for comparison."""
    return text.replace("\xa0", " ").strip().lower()


def _build_colmap(row):
    """Map column indexes to fields for one candidate header row.

    Returns None when the row does not look like a header. Several columns
    can map to "time" (MT5 position tables have an open and a close Time).
    """
    colmap = {}
    used = set()
    for idx, cell in enumerate(row):
        # compare with and without spaces so "S / L" matches "s/l"
        name = _clean(cell).replace(" ", "")
        if not name:
            continue
        for field, aliases in COLUMN_ALIASES.items():
            if name in [a.replace(" ", "") for a in aliases]:
                if field == "time":
                    # multiple time columns are fine and wanted
                    colmap.setdefault("time_cols", []).append(idx)
                    used.add(("time", idx))
                elif field not in colmap:
                    colmap[field] = idx
                    used.add((field, idx))
                break
    if len(used) < 3 or "time_cols" not in colmap or "profit" not in colmap:
        return None
    return colmap


def _to_float(text):
    """Parse a number like '-1 234.56', '1,234.56' or '0.10'. None if not one."""
    if text is None:
        return None
    t = text.replace("\xa0", " ").replace(" ", "").replace(",", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        # last resort: pull the first number-looking token out of the cell
        m = re.search(r"-?\d+(?:\.\d+)?", t)
        return float(m.group(0)) if m else None


DT_FORMATS = (
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%d.%m.%Y %H:%M:%S",
    "%d.%m.%Y %H:%M",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%Y.%m.%d",
    "%Y-%m-%d",
)


def _to_datetime(text):
    """Parse the date formats MT5 reports use. None if not a date."""
    t = text.replace("\xa0", " ").strip()
    # drop fractional seconds if present (2024.01.15 10:30:00.123)
    t = re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", t)
    for fmt in DT_FORMATS:
        try:
            return datetime.strptime(t, fmt)
        except ValueError:
            continue
    return None


def _row_to_trade(cells, colmap):
    """Turn one table row into a Trade, or None if it is not a trade row."""
    times = []
    for idx in colmap["time_cols"]:
        if idx < len(cells):
            dt = _to_datetime(cells[idx])
            if dt:
                times.append(dt)
    if not times:
        return None  # header rows, section titles, balance deposits

    profit = _to_float(cells[colmap["profit"]]) if colmap["profit"] < len(cells) else None
    if profit is None:
        return None

    symbol = cells[colmap["symbol"]] if "symbol" in colmap and colmap["symbol"] < len(cells) else ""
    if not symbol or _clean(symbol) in ("balance", "credit"):
        return None  # deposits and credits, not trades

    type_text = _clean(cells[colmap["type"]]) if "type" in colmap and colmap["type"] < len(cells) else ""
    if type_text in ("balance", "credit"):
        return None

    side = ""
    if "buy" in type_text:
        side = "buy"
    elif "sell" in type_text:
        side = "sell"

    # Deal-style tables mark opening rows with direction "in" (profit is
    # empty there anyway); closing rows say "out". Keep only the closing ones.
    if "direction" in colmap and colmap["direction"] < len(cells):
        d = _clean(cells[colmap["direction"]])
        if d and "out" not in d:
            return None

    def num(field):
        idx = colmap.get(field)
        if idx is None or idx >= len(cells):
            return 0.0
        return _to_float(cells[idx]) or 0.0

    return Trade(
        open_time=times[0],
        close_time=times[-1],
        symbol=symbol,
        side=side,
        volume=num("volume"),
        entry=num("price"),
        sl=num("sl"),
        tp=num("tp"),
        profit=profit,
    )


def _read_report_text(path):
    """Read the report file. MT5 often saves HTML as UTF-16, so try that first."""
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_report(path):
    """Parse an MT5 HTML report into a list of closed trades, oldest first."""
    html = _read_report_text(path)
    scraper = _TableScraper()
    scraper.feed(html)

    best = []
    for table in scraper.tables:
        trades = []
        colmap = None
        for row in table:
            candidate = _build_colmap(row)
            if candidate:
                colmap = candidate  # reports repeat headers on page breaks
                continue
            if colmap:
                trade = _row_to_trade(row, colmap)
                if trade:
                    trades.append(trade)
        if len(trades) > len(best):
            best = trades

    best.sort(key=lambda t: t.close_time)
    return best


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def compute_stats(trades):
    """Compute every number the dashboard shows, from a list of Trades."""
    profits = [t.profit for t in trades]
    wins = [p for p in profits if p > 0]
    losses = [p for p in profits if p < 0]

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(profits)

    # equity curve and max drawdown (in money, from a starting point of 0)
    equity = []
    peak = 0.0
    max_dd = 0.0
    running = 0.0
    for p in profits:
        running += p
        equity.append(running)
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)

    # per-hour stats, using the close time of each trade
    hourly = []
    for hour in range(24):
        bucket = [t for t in trades if t.close_time.hour == hour]
        hourly.append(_bucket_stats(hour, bucket))

    daily = []
    for wd, name in enumerate(WEEKDAYS):
        bucket = [t for t in trades if t.close_time.weekday() == wd]
        daily.append(_bucket_stats(name, bucket))

    # planned risk:reward from S/L and T/P prices, where the report has them
    planned_rr = []
    for t in trades:
        if t.entry and t.sl and t.tp:
            risk = abs(t.entry - t.sl)
            reward = abs(t.tp - t.entry)
            if risk > 0:
                planned_rr.append(reward / risk)

    # realized R:R, the honest way to measure it without contract specs:
    # average winner divided by average loser
    realized_rr = None
    if wins and losses:
        realized_rr = (sum(wins) / len(wins)) / (gross_loss / len(losses))

    trade_hours = [h for h in hourly if h["trades"]]
    best_hour = max(trade_hours, key=lambda h: h["net"]) if trade_hours else None
    worst_hour = min(trade_hours, key=lambda h: h["net"]) if trade_hours else None

    return {
        "total_trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": sum(1 for p in profits if p == 0),
        "win_rate": len(wins) / len(wins + losses) if (wins or losses) else None,
        "net_profit": net,
        "avg_profit": net / len(trades) if trades else None,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "largest_win": max(wins, default=None),
        "largest_loss": min(losses, default=None),
        "max_drawdown": max_dd,
        "realized_rr": realized_rr,
        "avg_planned_rr": (sum(planned_rr) / len(planned_rr)) if planned_rr else None,
        "hourly": hourly,
        "daily": daily,
        "best_hour": best_hour,
        "worst_hour": worst_hour,
        "equity": equity,
        "first_trade": trades[0].close_time if trades else None,
        "last_trade": trades[-1].close_time if trades else None,
        "symbols": sorted({t.symbol for t in trades}),
    }


def _bucket_stats(label, bucket):
    """Win rate / net profit for one group of trades (an hour or a weekday)."""
    wins = sum(1 for t in bucket if t.profit > 0)
    losses = sum(1 for t in bucket if t.profit < 0)
    return {
        "label": label,
        "trades": len(bucket),
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / (wins + losses)) if (wins + losses) else None,
        "net": sum(t.profit for t in bucket),
    }


# ---------------------------------------------------------------------------
# Demo data (so the dashboard can be tried without a real report)
# ---------------------------------------------------------------------------


def demo_trades(count=120, seed=7):
    """Generate plausible XAUUSD trades with a realistic hour/weekday skew."""
    rnd = random.Random(seed)
    trades = []
    price = 2350.0
    now = datetime.now().replace(second=0, microsecond=0)
    # walk backwards so the sample always ends "today"
    t = now - timedelta(days=60)

    while len(trades) < count:
        # skip weekends, gold is closed
        t += timedelta(minutes=rnd.randint(45, 400))
        if t.weekday() >= 5:
            t += timedelta(days=2)
            continue

        price += rnd.uniform(-8, 8)  # slow random walk of the gold price
        side = rnd.choice(["buy", "sell"])
        volume = rnd.choice([0.1, 0.2, 0.3])
        risk_points = rnd.uniform(3.0, 9.0)          # SL distance in $ of gold
        rr = rnd.uniform(1.2, 2.5)                   # planned reward:risk
        entry = round(price, 2)
        if side == "buy":
            sl = round(entry - risk_points, 2)
            tp = round(entry + risk_points * rr, 2)
        else:
            sl = round(entry + risk_points, 2)
            tp = round(entry - risk_points * rr, 2)

        # dollar value: 1 lot of gold = $100 per $1 move
        risk_money = risk_points * 100 * volume

        # win rate sits a bit above break-even; mornings trade slightly better
        win_chance = 0.50 + (0.08 if 9 <= t.hour < 12 else 0) \
                         + (0.05 if 14 <= t.hour < 17 else 0) \
                         - (0.07 if t.hour in (0, 1, 22, 23) else 0)
        if rnd.random() < win_chance:
            profit = risk_money * rr * rnd.uniform(0.9, 1.0)
        else:
            profit = -risk_money * rnd.uniform(0.9, 1.05)
        profit = round(profit, 2)

        hold = timedelta(minutes=rnd.randint(15, 300))
        close = min(t + hold, now)
        trades.append(Trade(t, close, "XAUUSD", side, volume, entry, sl, tp, profit))

    trades.sort(key=lambda tr: tr.close_time)
    return trades


# ---------------------------------------------------------------------------
# Dashboard rendering
# ---------------------------------------------------------------------------

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trade Journal</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {
    --bg: #0f1420; --panel: #171e2e; --line: #26304a;
    --text: #e7ecf5; --muted: #8b96ad;
    --green: #2fbf71; --red: #e05252; --blue: #4f8cff;
  }
  * { box-sizing: border-box; }
  body { margin: 0; padding: 24px; background: var(--bg); color: var(--text);
         font: 15px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
  h1 { font-size: 22px; margin: 0 0 4px; }
  .sub { color: var(--muted); margin: 0 0 20px; font-size: 13px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 12px; margin-bottom: 20px; }
  .card { background: var(--panel); border: 1px solid var(--line);
          border-radius: 10px; padding: 14px 16px; }
  .card .k { color: var(--muted); font-size: 12px; text-transform: uppercase;
             letter-spacing: .04em; }
  .card .v { font-size: 22px; font-weight: 600; margin-top: 4px; }
  .pos { color: var(--green); } .neg { color: var(--red); }
  .grid { display: grid; grid-template-columns: 1fr; gap: 20px; }
  .panel { background: var(--panel); border: 1px solid var(--line);
           border-radius: 10px; padding: 18px; }
  .panel h2 { font-size: 14px; margin: 0 0 12px; color: var(--muted);
              text-transform: uppercase; letter-spacing: .05em; }
  .chart-wrap { position: relative; height: 320px; }
  table { width: 100%; border-collapse: collapse; font-size: 14px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--line); }
  th { color: var(--muted); font-weight: 500; font-size: 12px;
       text-transform: uppercase; letter-spacing: .04em; }
  td.num, th.num { text-align: right; }
  .foot { color: var(--muted); font-size: 12px; margin-top: 20px; }
</style>
</head>
<body>
<h1>Trade Journal</h1>
<p class="sub">__SUBTITLE__</p>

<div class="cards" id="cards"></div>

<div class="grid">
  <div class="panel">
    <h2>Equity curve (cumulative profit)</h2>
    <div class="chart-wrap"><canvas id="equityChart"></canvas></div>
  </div>
  <div class="panel">
    <h2>Net profit by hour of day (server/report time)</h2>
    <div class="chart-wrap"><canvas id="hourChart"></canvas></div>
  </div>
  <div class="panel">
    <h2>Weekday performance</h2>
    <table id="weekdayTable">
      <thead><tr>
        <th>Day</th><th class="num">Trades</th><th class="num">Win rate</th>
        <th class="num">Net profit</th><th class="num">Avg per trade</th>
      </tr></thead>
      <tbody></tbody>
    </table>
  </div>
</div>

<p class="foot">Charts are drawn with Chart.js loaded from a CDN, so they need an
internet connection on first view. Data comes from __SOURCE__.</p>

<script>
const DATA = __DATA__;

function money(v) {
  if (v === null || v === undefined) return "-";
  const s = v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (v < 0 ? "-$" : "$") + s.replace("-", "");
}
function pct(v) { return v === null || v === undefined ? "-" : (v * 100).toFixed(1) + "%"; }
function num(v, d) { return v === null || v === undefined ? "-" : v.toFixed(d); }

const cards = [
  ["Total trades", DATA.total_trades],
  ["Win rate", pct(DATA.win_rate)],
  ["Net profit", money(DATA.net_profit), DATA.net_profit >= 0 ? "pos" : "neg"],
  ["Profit factor", num(DATA.profit_factor, 2)],
  ["Max drawdown", money(-DATA.max_drawdown), "neg"],
  ["Largest win", money(DATA.largest_win), "pos"],
  ["Largest loss", money(DATA.largest_loss), "neg"],
  ["Realized R:R", num(DATA.realized_rr, 2)],
  ["Avg planned R:R", num(DATA.avg_planned_rr, 2)],
  ["Best hour", DATA.best_hour ? DATA.best_hour.label + ":00 (" + money(DATA.best_hour.net) + ")" : "-"],
  ["Worst hour", DATA.worst_hour ? DATA.worst_hour.label + ":00 (" + money(DATA.worst_hour.net) + ")" : "-"],
];
document.getElementById("cards").innerHTML = cards.map(c =>
  `<div class="card"><div class="k">${c[0]}</div><div class="v ${c[2] || ""}">${c[1]}</div></div>`
).join("");

const wdBody = document.querySelector("#weekdayTable tbody");
wdBody.innerHTML = DATA.daily.map(d => {
  const avg = d.trades ? d.net / d.trades : null;
  const cls = d.net > 0 ? "pos" : (d.net < 0 ? "neg" : "");
  return `<tr><td>${d.label}</td><td class="num">${d.trades}</td>` +
         `<td class="num">${pct(d.win_rate)}</td>` +
         `<td class="num ${cls}">${money(d.net)}</td>` +
         `<td class="num">${money(avg)}</td></tr>`;
}).join("");

if (window.Chart) {
  Chart.defaults.color = "#8b96ad";
  Chart.defaults.borderColor = "#26304a";

  new Chart(document.getElementById("equityChart"), {
    type: "line",
    data: {
      labels: DATA.equity_labels,
      datasets: [{
        label: "Cumulative profit",
        data: DATA.equity,
        borderColor: "#4f8cff",
        backgroundColor: "rgba(79,140,255,.12)",
        fill: true, pointRadius: 0, tension: 0.1
      }]
    },
    options: {
      plugins: { legend: { display: false },
        tooltip: { callbacks: { title: items => "Trade " + (items[0].dataIndex + 1) + " - " + DATA.equity_labels[items[0].dataIndex] } } },
      scales: { y: { ticks: { callback: v => "$" + v } } }
    }
  });

  new Chart(document.getElementById("hourChart"), {
    type: "bar",
    data: {
      labels: DATA.hourly.map(h => h.label),
      datasets: [{
        data: DATA.hourly.map(h => h.net),
        backgroundColor: DATA.hourly.map(h => h.net >= 0 ? "#2fbf71" : "#e05252"),
        borderRadius: 3
      }]
    },
    options: {
      plugins: { legend: { display: false },
        tooltip: { callbacks: {
          title: items => items[0].dataIndex + ":00",
          label: ctx => {
            const h = DATA.hourly[ctx.dataIndex];
            return [money(h.net), h.trades + " trades, " + pct(h.win_rate) + " win rate"];
          }
        } } },
      scales: { y: { ticks: { callback: v => "$" + v } } }
    }
  });
} else {
  document.querySelectorAll(".chart-wrap").forEach(el => {
    el.innerHTML = "<p>Charts need an internet connection (Chart.js is loaded from a CDN). " +
                   "The summary cards and tables above still show all the numbers.</p>";
  });
}
</script>
</body>
</html>
"""


def render_dashboard(stats, subtitle="", source=""):
    """Build the single-page dashboard HTML with all data embedded as JSON."""
    equity_labels = []
    equity_points = stats["equity"]
    return (
        _PAGE
        .replace("__SUBTITLE__", subtitle)
        .replace("__SOURCE__", source)
        .replace("__DATA__", json.dumps(_jsonify(stats)).replace("</", "<\\/"))
    )


def _jsonify(stats):
    """Make the stats dict JSON-safe (datetimes to strings, keep numbers)."""
    out = {}
    for key, value in stats.items():
        if isinstance(value, datetime):
            out[key] = value.strftime("%Y-%m-%d %H:%M")
        elif key == "hourly":
            out[key] = [{"label": f"{h['label']:02d}", **{k: v for k, v in h.items() if k != "label"}} for h in value]
        elif key in ("best_hour", "worst_hour") and value:
            out[key] = {**value, "label": f"{value['label']:02d}"}
        else:
            out[key] = value
    out["equity_labels"] = [
        f"{i + 1}" for i in range(len(out.get("equity", [])))
    ]
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Turn a MetaTrader 5 HTML trade-history report into an HTML dashboard."
    )
    parser.add_argument("report", nargs="?", help="path to the MT5 HTML report file")
    parser.add_argument("-o", "--output", default="dashboard.html",
                        help="output dashboard file (default: dashboard.html)")
    parser.add_argument("--demo", action="store_true",
                        help="generate realistic demo trades instead of reading a report")
    parser.add_argument("--demo-trades", type=int, default=120,
                        help="number of trades in demo mode (default: 120)")
    args = parser.parse_args(argv)

    if args.demo:
        trades = demo_trades(args.demo_trades)
        subtitle = "Demo data - not from a real account"
        source = "the built-in demo generator"
    elif args.report:
        trades = parse_report(args.report)
        subtitle = f"{len(trades)} closed trades"
        source = f"report: {Path(args.report).name}"
    else:
        parser.error("give me a report.html file, or use --demo to try it out")

    if not trades:
        print("No closed trades found in the report. "
              "Make sure it is the trade-history HTML report from MT5 "
              "(Toolbox > History > right-click > Report > HTML).", file=sys.stderr)
        return 1

    stats = compute_stats(trades)
    html = render_dashboard(stats, subtitle=subtitle, source=source)
    Path(args.output).write_text(html, encoding="utf-8")

    # short human-readable recap in the terminal
    net = stats["net_profit"]
    wr = stats["win_rate"]
    pf = stats["profit_factor"]
    print(f"Wrote {args.output}")
    print(f"Trades: {stats['total_trades']} | Win rate: "
          f"{(wr * 100 if wr is not None else 0):.1f}% | "
          f"Net: {net:.2f} | Profit factor: {pf if pf is not None else 'n/a'}")
    if stats["max_drawdown"]:
        print(f"Max drawdown: {stats['max_drawdown']:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
