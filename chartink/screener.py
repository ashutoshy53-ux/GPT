"""
Screen NSE stocks for a 4-hour 9/21 EMA bullish crossover combined with a
daily trend template and fundamental filters.

Runs against Yahoo Finance via yfinance. Written to be run locally: the
container this was authored in blocks every market data host at the network
policy layer, so nothing here has been executed against live data.

    pip install yfinance pandas
    python screener.py --universe nifty500.csv --out results.csv

The universe file is a text or CSV file with one NSE symbol per line, without
the .NS suffix. A header line named "Symbol" is tolerated, so the constituent
CSV that NSE publishes can be passed directly.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, asdict

import pandas as pd
import yfinance as yf

# Yahoo serves at most 730 days of 60-minute bars.
INTRADAY_PERIOD = "180d"
INTRADAY_INTERVAL = "60m"
DAILY_PERIOD = "2y"

FAST_EMA = 9
SLOW_EMA = 21
BARS_PER_4H = 4  # 60-minute bars grouped into one 4-hour candle

MIN_EPS_GROWTH = 0.15  # 15 percent, as a fraction
MAX_DEBT_TO_EQUITY = 1.0


@dataclass
class Hit:
    symbol: str
    close: float
    ema9_4h: float
    ema21_4h: float
    pct_above_52w_low: float
    pct_below_52w_high: float
    debt_to_equity: float | None
    eps_growth: float | None
    trailing_pe: float | None


def load_universe(path: str) -> list[str]:
    with open(path) as fh:
        rows = [line.strip().strip('"').split(",")[0].strip() for line in fh if line.strip()]
    if rows and rows[0].lower() in {"symbol", "symbols", "ticker"}:
        rows = rows[1:]
    return [r.upper() for r in rows if r]


def to_4h(intraday: pd.DataFrame) -> pd.DataFrame:
    """Group 60-minute bars into 4-hour candles within each trading day.

    NSE trades 09:15 to 15:30, a 6h15m session, so a day yields seven hourly
    bars and therefore two 4-hour candles: a full one and a short tail. A
    calendar-aligned resample would instead straddle the session boundary and
    merge the close of one day with the open of the next, so the grouping is
    done per day.

    This is an approximation. A vendor that builds 4-hour candles differently
    will produce different bars, and therefore a different crossover date.
    """
    if intraday.empty:
        return intraday

    df = intraday.copy()
    df["_day"] = df.index.date
    df["_slot"] = df.groupby("_day").cumcount() // BARS_PER_4H

    grouped = df.groupby(["_day", "_slot"]).agg(
        Open=("Open", "first"),
        High=("High", "max"),
        Low=("Low", "min"),
        Close=("Close", "last"),
        Volume=("Volume", "sum"),
        ts=("Close", lambda s: s.index[0]),
    )
    return grouped.set_index("ts").sort_index()


def fresh_ema_cross(bars: pd.DataFrame) -> bool:
    """True when the fast EMA crossed above the slow EMA on the latest candle.

    The prior-candle condition is what makes this a signal rather than a
    description of an existing uptrend. Without it the screen returns every
    stock already trending.
    """
    if len(bars) < SLOW_EMA * 3:
        return False

    fast = bars["Close"].ewm(span=FAST_EMA, adjust=False).mean()
    slow = bars["Close"].ewm(span=SLOW_EMA, adjust=False).mean()

    return bool(fast.iloc[-1] > slow.iloc[-1] and fast.iloc[-2] <= slow.iloc[-2])


def trend_template(daily: pd.DataFrame) -> tuple[bool, float, float]:
    """Minervini's trend template, minus the RS rating.

    Returns (passes, percent above the 52-week low, percent below the 52-week high).
    RS rating has no equivalent in price data alone and is omitted rather than
    approximated silently.
    """
    if len(daily) < 200:
        return False, 0.0, 0.0

    close = daily["Close"]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma150 = close.rolling(150).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    sma200_month_ago = close.rolling(200).mean().iloc[-22]

    last = close.iloc[-1]
    window = close.tail(252)
    low52, high52 = window.min(), window.max()

    above_low = (last / low52 - 1) * 100
    below_high = (1 - last / high52) * 100

    passes = all([
        last > sma150,
        last > sma200,
        sma50 > sma150,
        sma150 > sma200,
        sma200 > sma200_month_ago,
        last >= low52 * 1.25,
        last >= high52 * 0.75,
    ])
    return passes, above_low, below_high


def fundamentals(ticker: yf.Ticker) -> tuple[float | None, float | None, float | None]:
    """Pull debt-to-equity, EPS growth and trailing P/E.

    Yahoo's fundamental coverage of Indian small and mid caps is patchy, and a
    missing field is not the same as a failing one. Missing values are returned
    as None and the caller decides how to treat them.
    """
    try:
        info = ticker.info
    except Exception:
        return None, None, None

    dte = info.get("debtToEquity")
    if dte is not None:
        dte = dte / 100.0  # Yahoo reports this as a percentage

    growth = info.get("earningsGrowth")
    if growth is None:
        growth = info.get("earningsQuarterlyGrowth")

    return dte, growth, info.get("trailingPE")


def screen(symbols: list[str], require_fundamentals: bool, pause: float) -> list[Hit]:
    hits: list[Hit] = []

    for i, sym in enumerate(symbols, 1):
        yahoo_sym = f"{sym}.NS"
        print(f"[{i}/{len(symbols)}] {yahoo_sym}", file=sys.stderr)

        try:
            ticker = yf.Ticker(yahoo_sym)
            daily = ticker.history(period=DAILY_PERIOD, interval="1d")
            if daily.empty:
                continue

            passes_trend, above_low, below_high = trend_template(daily)
            if not passes_trend:
                continue

            intraday = ticker.history(period=INTRADAY_PERIOD, interval=INTRADAY_INTERVAL)
            bars = to_4h(intraday)
            if not fresh_ema_cross(bars):
                continue

            dte, growth, pe = fundamentals(ticker)
            if require_fundamentals:
                if dte is None or growth is None:
                    continue
                if dte >= MAX_DEBT_TO_EQUITY or growth <= MIN_EPS_GROWTH:
                    continue

            close = bars["Close"]
            fast = close.ewm(span=FAST_EMA, adjust=False).mean()
            slow = close.ewm(span=SLOW_EMA, adjust=False).mean()

            hits.append(Hit(
                symbol=sym,
                close=round(float(close.iloc[-1]), 2),
                ema9_4h=round(float(fast.iloc[-1]), 2),
                ema21_4h=round(float(slow.iloc[-1]), 2),
                pct_above_52w_low=round(above_low, 1),
                pct_below_52w_high=round(below_high, 1),
                debt_to_equity=round(dte, 2) if dte is not None else None,
                eps_growth=round(growth, 3) if growth is not None else None,
                trailing_pe=round(pe, 1) if pe is not None else None,
            ))
            print(f"    HIT {sym}", file=sys.stderr)

        except Exception as exc:
            print(f"    skipped {sym}: {exc}", file=sys.stderr)

        time.sleep(pause)

    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--universe", required=True, help="file of NSE symbols, one per line, no .NS suffix")
    ap.add_argument("--out", default="results.csv")
    ap.add_argument("--limit", type=int, help="screen only the first N symbols, for a quick test")
    ap.add_argument("--skip-fundamentals", action="store_true",
                    help="run the technical screen only, ignoring debt-to-equity and EPS growth")
    ap.add_argument("--pause", type=float, default=0.4, help="seconds between symbols")
    args = ap.parse_args()

    symbols = load_universe(args.universe)
    if args.limit:
        symbols = symbols[:args.limit]
    print(f"screening {len(symbols)} symbols", file=sys.stderr)

    hits = screen(symbols, not args.skip_fundamentals, args.pause)

    if not hits:
        print("no matches", file=sys.stderr)
        return 0

    df = pd.DataFrame([asdict(h) for h in hits])
    df.to_csv(args.out, index=False)
    print(df.to_string(index=False))
    print(f"\n{len(hits)} matches written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
