# Trade Journal

A small Python tool that turns a MetaTrader 5 trade-history HTML report into a
single-page dashboard: win rate, profit factor, max drawdown, best and worst
trading hours, weekday performance, and risk-reward numbers.

No account login, no server, nothing leaves your machine. The script reads the
report file and writes one HTML file you can open in a browser.

## What it shows

- Summary cards: total trades, win rate, net profit, profit factor, max
  drawdown, largest win/loss, realized and planned R:R, best/worst hour
- Equity curve (cumulative profit, trade by trade)
- Net profit by hour of day, with win rate in the tooltips
- Weekday table: trades, win rate, net and average profit per day

## Quick start

Requires Python 3.8+ (standard library only, nothing to install).

Try it without a report:

```
python trade_journal.py --demo -o demo.html
```

Use your own data: in MT5, open the Toolbox, go to History, right-click,
choose Report and save it as HTML. Then:

```
python trade_journal.py report.html -o dashboard.html
```

Open `dashboard.html` in a browser. Charts are drawn with Chart.js loaded
from a CDN, so the two charts need an internet connection on first view;
the summary cards and the weekday table work offline.

## How it works

The script reads the HTML with `html.parser`, finds the trade table by its
header row, and maps the columns by name, so it handles both report layouts:

- the Positions table (`Time, Position, Symbol, Type, Volume, Price, S/L,
  T/P, Time, Price, Profit`), and
- the Deals table (`Time, Deal, Symbol, Type, Direction, Volume, Price,
  Commission, Swap, Profit`), where it keeps only the closing ("out") deals.

Balance and credit rows are ignored, dates and numbers are parsed in the
formats MT5 writes them (including UTF-16 encoded files and space-separated
thousands like `1 234.56`).

Two honest notes on the R:R numbers: "realized R:R" is the average winner
divided by the average loser, and "planned R:R" only appears when the report
contains entry, S/L and T/P prices (the Deals layout does not carry them).
The report's timestamps are in your broker's server time, so hour-of-day
stats follow that, not your local clock.

## Tests

```
python -m unittest discover tests
```

The tests parse two synthetic MT5-style reports in `tests/`, check the
statistics math, and run the CLI end to end in demo and report mode.

## Screenshots

_(placeholder: add a screenshot of `demo.html` here)_

## Project layout

```
trade_journal.py        parser, statistics and dashboard in one file
tests/
  make_sample_report.py generates synthetic MT5-style reports
  sample_report.html    sample report (Positions layout)
  sample_report_deals.html  sample report (Deals layout)
  test_trade_journal.py test suite
requirements.txt        empty on purpose: standard library only
```

## License

MIT
