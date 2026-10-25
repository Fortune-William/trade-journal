#!/usr/bin/env python3
"""Generate synthetic MT5-style HTML reports used by the tests.

Creates two files next to this script:
  sample_report.html       - the MT5 "Positions" table layout
                             (Time | Position | Symbol | Type | Volume |
                              Price | S/L | T/P | Time | Price | Profit)
  sample_report_deals.html - the MT5 "Deals" table layout
                             (Time | Deal | Symbol | Type | Direction |
                              Volume | Price | Commission | Swap | Profit)
"""

import random
from datetime import datetime, timedelta
from pathlib import Path


def _fmt_dt(dt):
    """MT5's usual report date format."""
    return dt.strftime("%Y.%m.%d %H:%M:%S")


def make_positions_report(count=40, seed=11):
    """An HTML report shaped like the MT5 position history table."""
    rnd = random.Random(seed)
    start = datetime(2024, 1, 2, 8, 0, 0)
    rows = []
    price = 2050.0
    position_id = 500100
    t = start

    # a deposit row, like a real report has
    rows.append(
        f"<tr><td>{_fmt_dt(start)}</td><td></td><td></td><td>Balance</td>"
        f"<td></td><td></td><td></td><td></td><td></td><td></td>"
        f"<td>1 000.00</td></tr>"
    )

    for _ in range(count):
        t += timedelta(hours=rnd.randint(3, 30))
        if t.weekday() >= 5:  # skip weekends
            t += timedelta(days=2)
        price += rnd.uniform(-6, 6)
        side = rnd.choice(["buy", "sell"])
        volume = rnd.choice(["0.10", "0.20"])
        entry = round(price, 2)
        risk = rnd.uniform(2.0, 8.0)
        rr = rnd.uniform(1.2, 2.5)
        if side == "buy":
            sl = round(entry - risk, 2)
            tp = round(entry + risk * rr, 2)
        else:
            sl = round(entry + risk, 2)
            tp = round(entry - risk * rr, 2)

        hold = timedelta(minutes=rnd.randint(20, 240))
        close = t + hold
        exit_price = round(price + rnd.uniform(-risk, risk * rr), 2)
        move = (exit_price - entry) * (1 if side == "buy" else -1)
        profit = round(move * 100 * float(volume), 2)
        position_id += rnd.randint(1, 5)

        rows.append(
            f"<tr><td>{_fmt_dt(t)}</td><td>{position_id}</td><td>XAUUSD</td>"
            f"<td>{side}</td><td>{volume}</td><td>{entry:.2f}</td>"
            f"<td>{sl:.2f}</td><td>{tp:.2f}</td>"
            f"<td>{_fmt_dt(close)}</td><td>{exit_price:.2f}</td>"
            f"<td>{profit:.2f}</td></tr>"
        )

    head = (
        "<html><head><meta charset='utf-8'><title>Statement</title></head><body>"
        "<h1>Trading Statement</h1>"
        "<table><tr>"
        "<th>Time</th><th>Position</th><th>Symbol</th><th>Type</th>"
        "<th>Volume</th><th>Price</th><th>S / L</th><th>T / P</th>"
        "<th>Time</th><th>Price</th><th>Profit</th>"
        "</tr>"
    )
    return head + "\n".join(rows) + "\n</table></body></html>"


def make_deals_report(count=25, seed=23):
    """An HTML report shaped like the MT5 deals history table."""
    rnd = random.Random(seed)
    start = datetime(2024, 3, 4, 9, 0, 0)
    rows = []
    t = start
    deal_id = 9000
    order_id = 7000
    price = 2150.0

    rows.append(
        f"<tr><td>{_fmt_dt(start)}</td><td></td><td></td><td>Balance</td>"
        f"<td></td><td></td><td></td><td></td><td></td><td></td>"
        f"<td>500.00</td></tr>"
    )

    for _ in range(count):
        price += rnd.uniform(-5, 5)
        side = rnd.choice(["buy", "sell"])
        volume = rnd.choice(["0.10", "0.30"])

        # opening deal: direction "in", no profit
        t += timedelta(hours=rnd.randint(2, 20))
        deal_id += 1
        order_id += 1
        entry = round(price, 2)
        rows.append(
            f"<tr><td>{_fmt_dt(t)}</td><td>{deal_id}</td><td>XAUUSD</td>"
            f"<td>{side}</td><td>in</td><td>{volume}</td><td>{entry:.2f}</td>"
            f"<td>{order_id}</td><td>-0.20</td><td>0.00</td><td></td></tr>"
        )

        # closing deal: direction "out", carries the profit
        t += timedelta(minutes=rnd.randint(30, 500))
        deal_id += 1
        order_id += 1
        exit_price = round(price + rnd.uniform(-6, 6), 2)
        move = (exit_price - entry) * (1 if side == "buy" else -1)
        profit = round(move * 100 * float(volume) - 0.4, 2)
        rows.append(
            f"<tr><td>{_fmt_dt(t)}</td><td>{deal_id}</td><td>XAUUSD</td>"
            f"<td>{'sell' if side == 'buy' else 'buy'}</td><td>out</td>"
            f"<td>{volume}</td><td>{exit_price:.2f}</td><td>{order_id}</td>"
            f"<td>-0.20</td><td>0.00</td><td>{profit:.2f}</td></tr>"
        )

    head = (
        "<html><head><meta charset='utf-8'><title>Statement</title></head><body>"
        "<h1>Deals</h1>"
        "<table><tr>"
        "<th>Time</th><th>Deal</th><th>Symbol</th><th>Type</th><th>Direction</th>"
        "<th>Volume</th><th>Price</th><th>Order</th><th>Commission</th>"
        "<th>Swap</th><th>Profit</th>"
        "</tr>"
    )
    return head + "\n".join(rows) + "\n</table></body></html>"


if __name__ == "__main__":
    here = Path(__file__).parent
    (here / "sample_report.html").write_text(make_positions_report(), encoding="utf-8")
    (here / "sample_report_deals.html").write_text(make_deals_report(), encoding="utf-8")
    print("Wrote sample_report.html and sample_report_deals.html")
