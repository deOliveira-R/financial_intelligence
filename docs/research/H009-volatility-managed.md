# H009: volatility-managed exposure earns more per unit of risk than a fixed allocation

Status: **pre-registered 2026-10-22, not yet run.** Track: investing, the regime dial of
the decision stack after H006's rejection (candidates.md row q). Validation mode A.

## Claim

Setting the equity weight each month to a volatility target divided by last month's
realized volatility, capped at fully invested and never leveraged, with the rest in
T-bills, gives a higher Sharpe ratio and a smaller maximum drawdown than both buy-and-hold
and a constant allocation at the same average exposure, on the periods where H006's binary
trend filter failed (2016-2023). The return itself is expected to be lower than
buy-and-hold's (a scaler that never leverages gives up return in calm years); the claim is
about return per unit of risk and about drawdowns, judged against the constant mix.

## Mechanism

Volatility is persistent from one month to the next while expected returns don't rise in
proportion when volatility is high (the monthly risk-return tradeoff is flat or negative,
Moreira and Muir 2017), so a dollar invested in a calm month earns more per unit of risk
than one invested in a turbulent month. Scaling exposure by inverse volatility equalizes
risk through time and, unlike a trend rule, has no threshold to whipsaw around: the
weight falls as volatility rises and recovers as it subsides. It persists because it is
risk management rather than a mispricing, and because leverage-constrained investors can't
exploit calm periods. Its failure mode is a crash out of a calm state (February 2020: the
rule is fully invested when volatility jumps and only cuts at the next month end), which
the worst 12 months and the 2020 episode will show.

## Design

- **Assets:** SPY (from 1993) and RSP (from 2003), as in H006.
- **Rule, fixed:** at each month's last close, realized volatility = the annualized
  standard deviation of the last 21 daily log returns; weight for the next month =
  min(1, 12% / realized volatility). The 12% target is roughly the asset's long-run
  volatility less a margin, so the average weight sits near 0.8; it is set here, not
  fitted. Cash earns the 3-month T-bill (`DGS3MO`, previous day's value, as in H006).
  Costs: 5 basis points of the traded fraction each month.
- **Robustness, reported not chosen:** a 63-day volatility window; a 15% target.
- **Benchmarks:** buy and hold; the constant mix at the rule's realized average exposure,
  rebalanced monthly (H006's machinery: `studies/h006.py`).
- **Measures:** as H006: compound return, volatility, Sharpe ratio over T-bills, maximum
  drawdown, worst 12 months, exposure; the Sharpe difference and the drawdown ratio
  against each benchmark with block-bootstrap intervals (24-month blocks) from monthly
  return triples; the expanding view by year.

## Periods

As H006: design 1993-12 to 2015-12 (SPY; RSP from 2004-03), validation 2016-01 to
2023-06 (once), holdout 2023-07 onward (once, after validation whatever it shows; the rule
is fixed, nothing is chosen between periods).

## What would count

- **Support:** on validation, for both assets: Sharpe ratio above buy-and-hold's and above
  the constant mix's, with the bootstrap interval of the difference against the constant
  mix above zero; maximum drawdown at most two thirds of buy-and-hold's and below the
  constant mix's.
- **Rejection:** Sharpe ratio at or below the constant mix's, or drawdown reduction under
  15% against buy-and-hold.
- **Partial:** support on one asset, or Sharpe higher but the interval including zero.

## Trials

One rule, two assets: one logged trial per period, plus the two robustness runs (counted,
not chosen from). Bonferroni over three.

## Limits stated in advance

- The 12% target isn't optimized but it is a choice; the 15% run shows whether the result
  depends on it.
- A monthly rule can't react inside the month; a crash from calm takes a full month's
  exposure. That cost is part of the result, not a reason to add a daily override.
- The holdout is one bull regime (2023-07 onward): read for the cost of underinvestment.
