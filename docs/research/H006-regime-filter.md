# H006: a trend and credit regime filter cuts drawdowns at little cost in return

Status: **pre-registered 2026-10-22, not yet run.** Track: investing (the regime dial of
the decision stack, candidates.md row a). Validation mode A.

## Claim

Holding a broad US equity ETF only in months that begin with the index above its 200-day
average and high-yield credit spreads not widening, and T-bills otherwise, reduces the
maximum drawdown and the worst 12-month loss of buy-and-hold by at least a third while
the compound return after costs stays within one percentage point a year of buy-and-hold.
The reduction must exceed what a constant allocation with the same average exposure
achieves: otherwise the filter is only "owning less", not timing.

## Mechanism

Large drawdowns aren't instantaneous. Institutions cut risk slowly (risk limits,
redemptions, margin calls), volatility clusters, and bear markets are months in which
returns are lower and volatility higher. A trend filter is a crude but public reading of
"the market is in that state"; a widening credit spread is the bond market's reading of
the same thing, often earlier. The gain is mostly from avoiding high-volatility periods,
not from forecasting returns (Faber 2007; Moskowitz, Ooi and Pedersen 2012). The price:
whipsaws in sideways markets and missing V-shaped rebounds (2020, 2023), which is why the
return criterion is "within a point", not "better".

## Design

- **Assets:** SPY (total-return adjusted closes from 1993) as the primary; RSP (equal
  weight, from 2003) must agree, since a cap-weighted index concentrated in a few names
  (theses.md, thesis 5) can behave unlike the typical stock.
- **Inputs, as known at each month's last close:** the asset's close against its 200-day
  simple moving average; the ICE BofA high-yield option-adjusted spread (FRED
  `BAMLH0A0HYM2`, point in time: FRED's value as published by that day) against its own
  200-day average ("widening" when above).
- **Decisions monthly** at the last trading day's close, held for the following month
  (the low-attention track), with the position changed at that close as the daily
  backtester does. Cash earns the 3-month T-bill (`DGS3MO`, point in time) by the day.
  Costs: 5 basis points of the notional each time the position changes (ETF spread and
  commission).
- **Variants, fixed here (three):** V1 trend only; V2 credit only; V3 both must be
  benign (the most defensive). The 200-day window is fixed. Reported, not chosen:
  windows of 150 and 250 days, and daily instead of monthly decisions.
- **Benchmarks:** buy and hold of the same asset; and a **constant mix** at the
  variant's own realized average exposure, rebalanced monthly (the fair test of timing).
- **Measures:** compound annual return, annualized volatility, Sharpe ratio over T-bills,
  maximum drawdown (on daily equity), worst 12-month return, average exposure, position
  changes a year. Differences against both benchmarks with intervals from a moving-block
  bootstrap of the monthly return triples (strategy, buy-and-hold, constant mix; blocks
  of 24 months): the ratio of maximum drawdowns and the difference in compound return.
  The Newey-West mean of monthly return differences alongside.
- **Expanding-window view:** for each year from 2005, the statistics on the data to that
  year end and the next year's outcome, to show what the evidence said at each point.

## Periods

- **Design:** decisions from 1997-10 (the spread series begins 1996-12 and needs 200
  days) to 2015-12 for SPY; from 2004-02 for RSP. The variant is chosen here: the one
  with the largest drawdown reduction among those whose compound-return gap to
  buy-and-hold is within one point.
- **Validation:** decisions 2016-01 to 2023-06 (three drawdowns: 2018, 2020, 2022), run
  once on the chosen variant, which is then frozen.
- **Holdout:** 2023-07 onward, once. One bull regime with one correction (2025-04): read
  for the cost of lagging, not decisive on its own.

## What would count

- **Support:** on validation, for the chosen variant and for both assets: maximum
  drawdown at most two thirds of buy-and-hold's, with the bootstrap interval of the ratio
  below 1; compound return within one point a year of buy-and-hold (interval including
  zero); and both the drawdown and the return better than the constant mix.
- **Rejection:** drawdown reduction under 15%, or a return gap worse than two points a
  year, or the constant mix doing as well on both measures.
- **Partial:** support on SPY but not RSP (or the reverse) is recorded as such; the
  filter then isn't a market-regime reading but an index-specific one.

## Trials

Three variants, two assets, the design choice: six logged trials, plus the robustness
runs (two windows and the daily version, each on three variants and two assets: twelve)
counted in the log although not chosen from. Bonferroni over the total.

## Risks to the test

- Few independent episodes: 1998, 2000-02, 2008, 2011, 2015 in design; 2018, 2020, 2022
  in validation. The block bootstrap respects the dependence but can't invent episodes;
  the interval widths are the honest statement.
- Spread data is point in time but the ETF closes are final adjusted prices (dividend
  adjustments are known; no look-ahead in a trend rule).
- A monthly rule's result depends somewhat on the calendar day; the daily version is
  reported to show the choice isn't carrying the result.
