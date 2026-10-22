# H009: volatility-managed exposure earns more per unit of risk than a fixed allocation

Status: **rejected as registered (2026-10-22); what stands is a consistent drawdown cut
of a third at a cost of 3-4 points a year, with no risk-adjusted gain.** Design 1993-2015:
Sharpe 0.51 against 0.41 (SPY), 0.68 against 0.49 (RSP, interval above zero), drawdowns
0.57 and 0.48 of buy-and-hold's. Validation 2016-2023 (once): SPY Sharpe 0.73 against
0.67 with the interval spanning zero (partial), RSP 0.48 against 0.57 (rejection);
drawdown ratios 0.61 and 0.68 with intervals below 1. Holdout (once): Sharpe below
buy-and-hold on both, return 2.4-3.7 points a year lower. See Results. Track: investing,
the regime dial of the decision stack after H006's rejection (candidates.md row q).
Validation mode A.

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

## Results (2026-10-22)

S = the rule, H = buy and hold, M = the constant mix at the rule's average exposure
(whose Sharpe ratio equals buy-and-hold's by construction, so the Sharpe test against M
is the test against H). Primary rule: 12% target, 21-day volatility, monthly.

| Period | Asset | CAGR S / H / M | Vol S / H | Sharpe S / H | Max DD S / H / M | DD ratio to H | Sharpe diff to H | Exposure | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| Design 1993-12 to 2015-12 | SPY | 8.4 / 8.9 / 7.8% | 12 / 19% | 0.51 / 0.41 | -32 / -55 / -46% | 0.57 [0.43, 0.72] | +0.13 [-0.02, +0.31] | 0.79 | partial |
| | RSP (from 2003) | 9.4 / 9.9 / 8.3% | 12 / 21% | 0.68 / 0.49 | -29 / -60 / -51% | 0.48 [0.32, 0.78] | +0.27 [+0.01, +0.46] | 0.79 | support |
| Validation 2016-01 to 2023-06 | SPY | 10.2 / 12.9 / 10.8% | 12 / 19% | 0.73 / 0.67 | -23 / -34 / -28% | 0.61 [0.54, 0.83] | +0.07 [-0.24, +0.29] | 0.80 | partial |
| | RSP | 6.8 / 11.2 / 9.4% | 13 / 20% | 0.48 / 0.57 | -26 / -39 / -32% | 0.68 [0.61, 0.95] | -0.11 [-0.39, +0.13] | 0.80 | **rejection** |
| Holdout 2023-07 to 2026-10 | SPY | 16.0 / 19.7 / 18.1% | 12 / 15% | 0.91 / 0.98 | -14 / -19 / -17% | (40 months, no interval) | -0.13 | 0.90 | rejection |
| | RSP | 11.1 / 13.5 / 12.9% | 12 / 14% | 0.56 / 0.66 | -15 / -18 / -17% | | -0.14 | 0.93 | rejection |

Robustness (reported, not chosen): the 63-day window did worse everywhere in validation
(SPY Sharpe 0.63, RSP drawdown ratio 0.83 with the interval reaching 1.19); the 15% target
gave up less return (SPY gap -1.9 points) and less drawdown protection (ratios 0.76-0.77).
Worst 12 months in validation: SPY -11.8% against -19.7%, RSP -16.5% against -28.6%: the
rule was at full weight when February 2020 arrived, cut to about a third for April and
May, and took a month to come back.

**Verdict: rejected as registered.** Support needed a Sharpe ratio above buy-and-hold's on
both assets with the interval above zero; SPY's gain was small and uncertain, RSP's was
negative, and the holdout went the other way on both. What did hold, in every period and
on both assets: a maximum drawdown a third to a half smaller than buy-and-hold's and below
the constant mix's, with no whipsaw cost (the weight falls and recovers smoothly). That is
"owning less when it is volatile", worth 3-4 points a year of return since 2016, which a
constant 80% allocation doesn't buy at the same price in drawdown. For the low-attention
track it is a legitimate dial for someone who values the drawdown cut more than the
return; it is not a free lunch, and the claim of a higher return per unit of risk failed.
Together with H006: since 2016 no monthly market-timing rule tested here has improved
risk-adjusted returns on US indices; drawdown control has only been bought with return.

## Limits stated in advance

- The 12% target isn't optimized but it is a choice; the 15% run shows whether the result
  depends on it.
- A monthly rule can't react inside the month; a crash from calm takes a full month's
  exposure. That cost is part of the result, not a reason to add a daily override.
- The holdout is one bull regime (2023-07 onward): read for the cost of underinvestment.
