# 4-Hour EMA Crossover with Trend Template and Fundamental Screens

A Chartink scan combining a 9/21 EMA bullish crossover on 4-hour candles with a
daily-timeframe approximation of Minervini's trend template and Lynch-style
fundamental conditions.

Status: draft. The clauses below were written from documented Chartink syntax
conventions but have not been executed against Chartink's parser. Verify each
block inside the scan builder before relying on the output. See
[Verification checklist](#verification-checklist).

If you would rather run the screen than trust an unverified clause,
`screener.py` in this directory implements the same logic against Yahoo
Finance. See [Running the screen locally](#9-running-the-screen-locally).

## 1. Syntax conventions used

Chartink offset notation differs between daily and intraday scans, and getting
this wrong is the most common cause of a parser error.

| Context | Offset form | Example |
| --- | --- | --- |
| Daily and above | `latest`, `1 day ago`, `2 day ago` | `low > ema( close , 200 ) and 1 day ago low <= 1 day ago ema( close , 200 )` |
| Intraday | `[0]` for the current candle, `[-1]` for the prior candle | `[0] 4 hour close > [-1] 4 hour close` |

Every term in an intraday clause carries both the offset and the timeframe
prefix, including the arguments passed to an indicator. `[0] 4 hour ema( [0] 4
hour close , 9 )` is the correct form. Writing the offset once and leaving the
inner argument bare will not parse.

An earlier version of this scan used `1 4 hour candle ago` as the offset. That
form is not part of Chartink's documented notation and has been replaced by the
bracket offsets throughout.

## 2. Technical block: 4-hour 9/21 EMA crossover

Freshness of the cross is what the second condition enforces. Without it the
scan returns every stock already in an uptrend rather than those crossing now.

```
( {cash} (
  [0] 4 hour close > [0] 4 hour ema( [0] 4 hour close , 9 )
  and [-1] 4 hour close <= [-1] 4 hour ema( [-1] 4 hour close , 9 )
  and [0] 4 hour ema( [0] 4 hour close , 9 ) > [0] 4 hour ema( [0] 4 hour close , 21 )
) )
```

## 3. Trend template block (daily)

Minervini's template is defined on daily data. Run this block on its own first
to confirm it returns a plausible universe before combining it with anything
else.

```
( {cash} (
  latest close > sma( latest close , 150 )
  and latest close > sma( latest close , 200 )
  and sma( latest close , 50 ) > sma( latest close , 150 )
  and sma( latest close , 150 ) > sma( latest close , 200 )
  and sma( latest close , 200 ) > 1 month ago sma( latest close , 200 )
  and latest close > 1.25 * 52 week low
  and latest close > 0.75 * 52 week high
) )
```

Two conditions from the original template are not expressible in native
Chartink fields:

1. **Relative strength rating.** Chartink has no IBD-style RS rating. The
   workable proxy is a ratio of the stock's return to the Nifty 500 return over
   3 or 6 months, built as a comparison against the index symbol. This changes
   the meaning of the filter, so treat results as approximate rather than
   equivalent.
2. **200-day SMA trending up for at least one month.** The `1 month ago sma(...)`
   term above is the intended proxy. Confirm that Chartink accepts a month
   offset on a moving average term; if it rejects it, substitute `22 day ago`.

## 4. Fundamental block

```
( {cash} (
  latest "Debt to equity ratio" < 1
  and latest "EPS growth" > 1.15
) )
```

Two things to check here.

**Field labels.** Chartink's fundamental fields must be written exactly as they
appear in the scan builder dropdown. The labels above are the most likely
match, but variants such as `Debt to Equity` or `Basic EPS growth` exist in
published community scans. Select the field from the dropdown once, read the
token Chartink inserts, and use that spelling.

**Growth is a ratio, not a percentage.** Chartink expresses EPS growth as a
multiple of the prior period, so a 15 percent increase is `> 1.15`, not `> 15`.
Writing `> 15` will silently return an empty or near-empty result set rather
than an error, which makes it easy to miss.

**PEG ratio** is not a native field. Constructing it requires a custom formula
dividing the P/E field by the EPS growth field, and Chartink's formula support
for cross-field arithmetic on fundamentals should be confirmed before assuming
it works.

## 5. Combined scan

Combining an intraday technical block with daily and fundamental terms in a
single clause is the part of this scan most likely to fail. If the parser
rejects it, the fallback is to run the daily and fundamental blocks as a saved
scan, then apply the 4-hour crossover to that shortlist as a second scan.

```
( {cash} (
  [0] 4 hour close > [0] 4 hour ema( [0] 4 hour close , 9 )
  and [-1] 4 hour close <= [-1] 4 hour ema( [-1] 4 hour close , 9 )
  and [0] 4 hour ema( [0] 4 hour close , 9 ) > [0] 4 hour ema( [0] 4 hour close , 21 )
  and latest close > sma( latest close , 150 )
  and latest close > sma( latest close , 200 )
  and sma( latest close , 50 ) > sma( latest close , 150 )
  and sma( latest close , 150 ) > sma( latest close , 200 )
  and latest close > 1.25 * 52 week low
  and latest close > 0.75 * 52 week high
  and latest "Debt to equity ratio" < 1
  and latest "EPS growth" > 1.15
) )
```

## 6. Verification checklist

Work through the blocks in this order. Each step isolates one failure mode.

1. Paste section 4 alone. Confirm the fundamental field names parse and the
   result count is non-zero. A zero count with no error points at the ratio
   versus percentage issue.
2. Paste section 3 alone on the daily timeframe. Confirm the trend template
   returns a universe in the expected range for current market conditions.
3. Paste section 2 alone with the scan timeframe set to 4 hour. Confirm the
   crossover fires on names you can verify on a chart.
4. Paste section 5. If it parses and the count is roughly the intersection of
   the three, the combination works. If it errors, fall back to the two-stage
   approach in section 5.

## 7. Data and access constraints

- Intraday scans update live only on Chartink's paid tier. Free-tier scans are
  delayed, which matters for a crossover signal intended to be acted on within
  the candle.
- 4-hour history depth on the free tier may be shallow enough that the 21-period
  EMA has insufficient seeding on recently listed names.
- Fundamental fields update on reporting cycles, not daily, so the fundamental
  block behaves as a slow-moving universe filter rather than a signal.

## 8. Sources

Syntax conventions were taken from Chartink's scanner documentation and
published community scans. Chartink was not reachable from the environment in
which this file was written, so the offset and field-name conventions are drawn
from secondary references and are flagged above where confidence is lower.

## 9. Running the screen locally

`screener.py` implements the same three filters against Yahoo Finance, so the
output can be checked against a chart rather than taken on trust.

```
pip install yfinance pandas
python screener.py --universe nifty500.csv --out results.csv
```

The universe file is one NSE symbol per line without the `.NS` suffix. A
`Symbol` header is tolerated, so NSE's published constituent CSV can be passed
directly. Useful flags:

- `--limit 25` screens a short slice first, which is the sane way to confirm
  the pipeline works before committing to a few hundred network round trips.
- `--skip-fundamentals` runs the technical screen alone. Worth using once,
  because Yahoo's fundamental coverage of Indian mid and small caps is thin and
  a missing field is silently treated as a failure otherwise.

### What was tested

The pure logic was exercised against synthetic bars: 4-hour grouping produces
two candles per NSE session with correct open, high, low and close
aggregation and no candle straddling a day boundary; the crossover detector
fires on the crossing candle and not the one after it, and returns false on a
pure downtrend and on a series too short to seed the slow EMA; the trend
template accepts a clean uptrend and rejects a downtrend, a short series, and a
stock only 10 percent off its 52-week low.

What was not tested is the live path. Every market data host is blocked at the
network policy layer in the environment where this was written, so no call to
Yahoo has been made. Expect to fix small things on first run.

### Where this differs from Chartink

- **4-hour candles are constructed, not fetched.** NSE trades a 6h15m session,
  which yields seven hourly bars and therefore one full 4-hour candle plus a
  2h15m tail. Chartink builds its 4-hour bars its own way, so the two will
  disagree on the exact crossover date for some names.
- **RS rating is omitted rather than approximated.** Minervini's eighth
  condition has no equivalent in price data alone, and a silent proxy would be
  worse than its absence.
- **Fundamentals come from Yahoo, not Chartink.** Yahoo reports debt-to-equity
  as a percentage, which the script divides by 100, and falls back from
  `earningsGrowth` to `earningsQuarterlyGrowth` when the annual figure is
  absent. These are different underlying numbers, so the fundamental filter is
  comparable in intent but not identical in effect.
