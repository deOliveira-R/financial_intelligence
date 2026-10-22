# H007: selling index puts pays more when implied volatility exceeds realized

Status: **pre-registered 2026-10-22; first design run void (PUT's file is daily only from
2007, see Amendments); design re-run on 2007-2015 pending.** Track: options
(candidates.md row h), the first use of Cboe's index histories. Validation mode A.

## Claims

- **(1) The premium exists.** Systematically writing cash-secured at-the-money S&P 500
  puts (Cboe's PUT index) earns a return over T-bills, with a smaller drawdown than the
  index. Well documented; here it is the baseline, not the finding.
- **(2) It is larger when insurance is expensive.** The next month's PUT return over
  T-bills rises with the variance risk premium known at the month end: VIX minus the
  realized volatility of the S&P 500 over the prior 21 trading days. Sorting months on
  that gap, the top third earns a clearly larger premium than the bottom third, and a
  rule that writes puts only when the gap is above its own history's median beats always
  writing them on a risk-adjusted basis.
- **(3) Secondary, short history:** the term structure (VIX3M minus VIX) carries the same
  information: contango (a positive slope) precedes larger premia than backwardation.
  VIX3M's file begins in 2009, so this is descriptive in design and thin in validation.

## Mechanism

Institutions buy index puts as insurance; the dealers who sell them hold inventory
against balance-sheet constraints and charge for it. So implied volatility exceeds the
volatility that follows, on average (Carr and Wu 2009; Bollerslev, Tauchen and Zhou
2009). The premium isn't constant: after shocks, implied volatility stays high while
realized volatility falls back, and the gap between them is the price of insurance
relative to the risk being insured. A seller paid more for the same risk earns more.
The tail is the price: a crash month costs several months of premium, which is why the
test reports drawdowns and the Sharpe ratio, not only means.

## Data

- `cboe:vix`, `cboe:vix3m`, `cboe:put` from Cboe's daily history files (volatility.py);
  index closes are known at the close.
- SPY total-return closes (`px:SPY`, from 1993) for realized volatility and the equity
  comparison; 3-month T-bill (`fred:DGS3MO`, point in time) for the risk-free return.
- Unit: the month. At each month's last trading day t: VRP_t = VIX_t minus the annualized
  standard deviation of SPY's last 21 daily log returns (in VIX's units, percentage
  points); slope_t = VIX3M_t minus VIX_t. Outcomes from t's close to the next month end:
  PUT's return minus the T-bill return (the premium harvested), PUT minus SPY (does
  writing puts beat owning the index), and SPY minus T-bills (does the signal time
  equities as such).

## Tests (six, fixed here)

1. **Unconditional:** mean monthly PUT excess return (Newey-West, one lag), its Sharpe
   ratio, maximum drawdown, and the same for PUT minus SPY. Descriptive baseline.
2. **Regression (decisive):** next month's PUT excess return on VRP_t, Newey-West errors,
   one-sided test for a positive slope.
3. **Sorts:** months in terciles of VRP_t by its percentile in the expanding history to
   date (at least 60 months of history before the first sort): mean premium per tercile
   with Newey-West intervals. Descriptive.
4. **Rule (decisive):** hold PUT when VRP_t exceeds its expanding median, T-bills
   otherwise; compound return, volatility, Sharpe ratio and maximum drawdown against
   always-PUT and against SPY; the Sharpe difference against always-PUT with a
   moving-block bootstrap interval (24-month blocks).
5. **Equities:** test 2 with SPY's excess return as the outcome. Descriptive: whether the
   money is in the put premium specifically or in timing equity exposure.
6. **Term structure (secondary):** tests 2 and 4 with slope_t, from 2009-10.

Tests 2 and 4 decide, each at p < 0.025 (Bonferroni over the two). No other variants:
no other volatility windows, thresholds or sort schemes are tried under H007.

## Periods

- **Design:** months from 1993-02 to 2012-12 (VIX from 1990, PUT from 1991, SPY from
  1993).
- **Validation:** 2013-01 to 2023-06, once.
- **Holdout:** 2023-07 onward, once, after validation whatever it shows (nothing is
  chosen between them).

## What would count

- **Support:** on validation, test 2's slope is positive with p < 0.025 and the same sign
  as in design, and test 4's rule has a higher Sharpe ratio than always-PUT (interval of
  the difference above zero). Then the holdout.
- **Rejection:** slope at or below zero or p ≥ 0.1, or the rule's Sharpe below
  always-PUT's.
- **Weak support:** the premium (test 1) holds but conditioning doesn't: the money is the
  plain premium with its tail, and sizing is the whole question.

## Amendments (2026-10-22, after a design run the data made void)

The first design run (1993-02 to 2012-12, logged as one trial) used Cboe's PUT history
file as if it were daily. It isn't before 2007: the file has seven values between 1991
and 2006 (one in 1991, 1994, 1997, 2001, 2004), then daily from 2007-01. Carried forward
by the series engine, the stale values made PUT's monthly returns a step function with
jumps of up to 54% (2001-01), so every PUT statistic of that run is meaningless and its
"rejection" verdict is withdrawn (not a result about the claim). The equity test (test 5,
SPY on VRP) doesn't involve PUT and stands: the slope was positive with t = 3.3 in
1993-2012, the Bollerslev-Tauchen-Zhou finding; it is descriptive here and goes to
candidates.md as its own hypothesis.

Changes, before any valid result:
- **Periods:** PUT is decisive from 2007: design 2007-01 to 2015-12 (108 months, with
  2008), validation 2016-01 to 2023-05 (89 months, with 2018, 2020 and 2022), holdout from
  2023-06. The decision months were already before 2023-07; the periods only start later.
- **Long history, descriptive:** the same six tests on **BXMD** (Cboe S&P 500 30-delta
  BuyWrite, daily since 1986-06), another short-volatility strategy on the index, from
  1993-02 through each period's end. It can't decide the claim (a different instrument,
  and it spans the decisive months too) but shows whether the pattern held over three
  decades. VIX3M begins 2009-09, so the term-structure tests have thin samples either way.
- Nothing else changes: the signal, the tests, the thresholds and the verdict rules are
  as registered.

## Limits stated in advance

- The PUT index is computed from bid-ask midpoints and rolls monthly at the money; a
  personal account pays the spread and may size differently. A conditional premium under
  0.3 percentage points a month isn't worth implementing whatever its t-statistic.
- Realized volatility from SPY approximates S&P 500 volatility (the ETF tracks within a
  few basis points).
- The premium's history includes 2008 and 2020 in different periods (design, validation):
  the holdout has no crash, so its reading of the tail is incomplete by construction.
