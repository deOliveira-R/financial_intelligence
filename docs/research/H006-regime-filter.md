# H006: a trend and credit regime filter cuts drawdowns at little cost in return

Status: **rejected as registered (2026-10-22).** The trend rule chosen on the design
(SPY drawdown 0.30 of buy-and-hold's, return 2.4 points a year higher, 1993-2015) failed
validation (2016-2023-06): return gap -4.7 points a year with the interval excluding zero,
drawdown ratio 0.91, a constant 79% allocation did better on both; the holdout
(2023-07 onward, run once for the record) cost another 7.9 points a year on SPY. See
Results. Track: investing (the regime dial of the decision stack, candidates.md row a).
Validation mode A.

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
  200-day average ("widening" when above). *Amended below: `BAA10Y` since 1986.*
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

## Amendments (before any results)

**2026-10-22, data:** FRED now carries the ICE BofA option-adjusted spreads only from
2023-10 (793 observations; a licensing change, which the backlog had noticed as "FRED
keeps ~3 years"). The credit input is instead **Moody's Baa corporate yield minus the
10-year Treasury (FRED `BAA10Y`, daily since 1986)**, the long-history credit spread; the
same 200-day rule applies. With it, SPY's design decisions start in **1993-11** (SPY begins
1993-01 and the average needs 200 days) rather than 1997-10, adding 1994 and 1998 to the
design episodes. Both the spread and the T-bill yield are unrevised daily series that
FRED posts the next morning, so "as known at the close" is the previous day's final
value, which is how they're read (ALFRED vintages for them begin in 2005 or later).

## Trials

Three variants, two assets, the design choice: six logged trials, plus the robustness
runs (two windows and the daily version, each on three variants and two assets: twelve)
counted in the log although not chosen from. Bonferroni over the total.

## Results

**Design (2026-10-22; SPY from 1993-12, RSP from 2004-03, through 2015-12; 12 logged
variants, each covering both assets).** S = strategy, H = buy and hold, M = the constant
mix at the strategy's exposure; the ratio and gap carry 95% block-bootstrap intervals.

| Rule, window, decisions | Asset | CAGR S / H / M | Max drawdown S / H / M | DD ratio to H | CAGR gap to H | Beats M? | Verdict |
|---|---|---|---|---|---|---|---|
| **trend, 200, monthly** | SPY | 11.4 / 8.9 / 7.6% | -19 / -55 / -45% | 0.30 [0.13, 0.71] | +2.4 [-2.4, +8.8] | yes | support |
| | RSP | 8.4 / 8.3 / 7.0% | -23 / -60 / -50% | 0.32 [0.25, 1.07] | +0.1 [-7.7, +8.6] | yes | partial (interval reaches 1) |
| credit, 200, monthly | SPY | 7.6 / 8.9 / 6.2% | -24 / -55 / -33% | 0.36 [0.14, 0.68] | -1.3 [-7.7, +5.7] | yes | partial (gap) |
| | RSP | 6.2 / 6.0 / 4.6% | -21 / -60 / -36% | 0.23 [0.10, 0.82] | +0.2 [-5.2, +5.9] | yes | support |
| both, 200, monthly | SPY | 7.9 / 8.9 / 5.8% | -19 / -55 / -30% | 0.21 [0.11, 0.55] | -1.0 [-7.7, +6.5] | yes | partial (gap) |
| | RSP | 6.7 / 8.3 / 5.4% | -21 / -60 / -37% | 0.23 [0.10, 0.84] | -1.6 [-10.9, +9.6] | yes | partial |

Robustness, not chosen from: 150-day windows did at least as well (all six variants
"support"; trend/150 SPY gap +1.3, both/150 SPY drawdown ratio 0.16); 250-day windows
similar to 200. **Daily decisions are worse everywhere** (7-9 switches a
year, return gaps of -1.5 to -3 points, two of six "rejection"): the whipsaw cost is real
and monthly decisions are the right cadence, which suits the low-attention track.

Reading: every monthly variant cut the maximum drawdown to a fifth to a third of
buy-and-hold's and beat the constant mix on both measures, so the reduction isn't "owning
less". The return gap is the uncertain part (intervals of about ±8 points a year on two
or three bear markets); in the design period the trend rule *added* return (it sat out
most of 2000-02 and 2008). The expanding view shows the price of the filter in calm
years: through 2005-2007 the ratio was above 1 (the 1998 and 2003-2005 whipsaws before
any bear market it could sidestep), then 0.2-0.25 from 2008 on.

**Choice (the registered rule):** among the 200-day monthly variants with an SPY gap within
a point, the largest SPY drawdown reduction: **trend** (0.30; "both" at 0.21 missed the gap
condition by a hair, -1.0 points). Frozen: `{"rule": "trend", "window": 200, "frequency":
"monthly"}`.

**Validation (2026-10-22, once; 2016-01 to 2023-06, 90 months):**

| Asset | CAGR S / H / M | Max drawdown S / H / M | DD ratio to H | CAGR gap to H | Beats M? | Verdict |
|---|---|---|---|---|---|---|
| SPY | 8.3 / 12.9 / 10.6% | -24 / -34 / -27% | 0.91 [0.41, 1.37] | -4.7 [-9.5, -0.5] | no (worse on both) | **rejection** |
| RSP | 5.9 / 11.2 / 9.0% | -23 / -39 / -30% | 0.77 [0.40, 1.07] | -5.3 [-8.0, +0.3] | no (worse on both) | **rejection** |

The worst 12 months were *worse* with the filter on SPY (-21.4% against -19.7%): it was
long through the February-March 2020 fall, exited at the end of March and missed April
and May. The yearly view says the same: 2019 +10.4% against +31.2% (out after the 2018
fourth quarter, back in after the rebound), 2020 +14.5% against +18.3%, the first half of
2023 +10.2% against +16.8%. Exposure 0.79 with 2.1 switches a year; the constant 79% mix
had a similar drawdown and 2.4 points a year more return.

**Holdout (2023-07 to 2026-10, 40 months, once, for the record):** SPY 11.8% against
19.7% a year (gap -7.9 points), daily maximum drawdown -10% against -19% (the filter halved
the April 2025 fall on daily data, exited at the end of April and missed May: on month-end
data the drawdowns were equal); RSP 11.8% against 13.5%, drawdown -9% against -18%.
Exposure 0.90. Too few months for intervals; read as the cost of lagging: 2 to 8 points a
year in a bull market.

**Verdict: rejected as registered.** The design-period edge came from two slow bear markets
(2000-02 and 2008), where a monthly 200-day rule steps out months before the bottom; every
drawdown since 2016 (2018 Q4, 2020, 2022, 2025) was fast or V-shaped, and the rule exits
after the damage and re-enters after the rebound. A constant allocation at the same average
exposure matched its drawdowns at a higher return, which is the registered test of "timing
rather than owning less", and it failed it. Not retuned: the 150-day variants looked better
in design, and whether any window survives 2016-2023 is a new hypothesis with its own
pre-registration, not a fix. What stands from the design: monthly decisions beat daily
ones everywhere (the whipsaw cost is 1.5-3 points a year), and binary trend filters are
regime-dependent. For the low-attention track the regime dial is better sought in
volatility-scaled exposure (candidates.md row q), judged on these same periods.

## Risks to the test

- Few independent episodes: 1998, 2000-02, 2008, 2011, 2015 in design; 2018, 2020, 2022
  in validation. The block bootstrap respects the dependence but can't invent episodes;
  the interval widths are the honest statement.
- Spread data is point in time but the ETF closes are final adjusted prices (dividend
  adjustments are known; no look-ahead in a trend rule).
- A monthly rule's result depends somewhat on the calendar day; the daily version is
  reported to show the choice isn't carrying the result.
