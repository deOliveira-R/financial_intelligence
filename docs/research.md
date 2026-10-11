# Research: signal design and validation (phase 5)

How signals are designed, tested and trusted. The goal is foresight: a signal earns trust
only if, using what was public at each moment, it would have told us something about what
came next, out of sample, beyond what simple known effects already say. Started
2026-10-11 with the user; read this with [plan.md](plan.md) when resuming.

## What the data can and can't test (audit, 2026-10-11)

| Data | History | Survivorship |
|---|---|---|
| Daily prices, whole market (Massive) | 2024-10 onward | free, but only two years |
| Daily prices, deep (Tiingo) | 1962 onward, 491 symbols | **biased**: today's large companies, plus ETFs |
| ETFs, indices, FRED (vintages), COT, contracts, bills, lobbying | 1990s-2015 onward | free (market and industry level) |
| SEC fundamentals | 2009 onward | **biased before ~2022**: 2.9k issuers in 2012, 15k in 2023 |
| Corporate events (8-K items, late filings, delistings) | 1993 onward | **biased**: fetched only for today's listings (5,547 issuers, all active) |
| Insider trades | 2006 onward | free (bulk dataset, every issuer) |
| 13F positions | 2001 onward | free (every reported holding) |

Consequences:

- **Market and industry signals can be tested now** on decades of ETF, index, macro and
  positioning data, which have no survivorship problem.
- **Stock-level signals can't be tested honestly yet.** The companies that went bankrupt,
  were delisted or deregistered are missing from events and fundamentals, and have no price
  history. A test on what's stored compares survivors with survivors and flatters anything
  correlated with survival. Step 1 below fixes this before any stock-level study.

## Protocol

1. **Pre-register.** Each hypothesis gets a file in `docs/research/` committed *before*
   its results exist (git history proves the order). It states the mechanism (who is
   wrong, and why the price doesn't already reflect it), the unit (stock, industry,
   market), the horizon, the benchmark, the data tier, the exact test, and what result
   would count as support or rejection. A hypothesis without a mechanism isn't tested.
2. **Point in time.** Every input is dated when it became public (filing dates, ALFRED
   vintages, publication lags). Entry is at the close after the public date.
3. **Holdout.** Events from **2023-07-01** onward are not examined during design. A
   hypothesis is frozen (parameters, thresholds, universe) and only then run once on the
   holdout. A failed holdout is recorded, not retuned.
4. **Fair benchmarks.** Equal-weight universe, sector or industry, and size-matched
   portfolios; SPY only alongside (cap-weighted, dominated by a few AI stocks in
   2023-2026). Industry signals are judged against the equal-weight market, market
   signals against buy and hold.
5. **Honest inference.** Overlapping horizons and events that cluster in time aren't
   independent: use calendar-time portfolios (one return per period) with
   Newey-West or block-bootstrap errors, never pooled event t-stats. Report effect size
   with its interval, the median next to the mean, and the hit rate.
6. **Count every trial.** Every run is logged (`research_runs`: hypothesis, parameters,
   period, result). Significance is judged against the number of variants tried
   (a t of 3 rather than 2 as the floor; deflated Sharpe for strategies).
7. **Beat the obvious.** A signal must add to what size, value, momentum, sector and the
   market already explain: report its effect after controlling for them.
8. **Robust, not tuned.** It should hold across sub-periods, nearby parameter values
   (not one magic threshold) and after realistic costs and liquidity.
9. **Foresight view.** Besides the full-sample result, show the expanding-window view:
   what the evidence available at each date said, and whether that belief held next.
10. **Kill fast.** Record failed hypotheses and why; they are results too.

## The two tracks and their outcomes

- **Investing (low attention).** What matters most is avoiding permanent losses and
  holding durable compounders. Outcomes: 1-3 year excess return against sector and
  size-matched peers, and *blowups*: deregistration, bankruptcy, delisting or a 50%+
  drawdown within 12 months. Exception alerts are judged by precision at a low alert rate
  (few alerts, most of them right), not by average returns.
- **Speculation (high attention).** Market and industry timing over days to weeks:
  positioning extremes, breadth, volatility, carry. Outcomes: forward returns of the
  traded instrument against buy and hold, after costs.
- **Policy and industry (the connective layer).** Congress, lobbying, contracts and bills
  aggregated by theme, judged on the theme's ETF against the equal-weight market over
  6-24 months. Few themes and years means low statistical power: treat it as evidence
  that accumulates, with the mechanism doing more work than the t-stat.

## Steps

1. **Survivorship-free research base** (prerequisite for stock-level work):
   - the population: every company that filed a periodic report since 2009 (EDGAR
     full-index), alive or dead;
   - their submissions (events, deregistrations, bankruptcies, SIC, names) and
     fundamentals (company facts);
   - quarterly prices from 13F (median value per share across managers, split-adjusted;
     13F values check out against known prices), mapped CUSIP to company;
   - outcomes per company and quarter: forward returns, blowups.
2. **Study harness**: calendar-time portfolios, clustered errors, benchmarks, holdout
   guard, trial log, expanding-window view.
3. **First hypotheses** (pre-registered one at a time):
   - market and industry, testable now: speculators' positioning extremes in commodity
     futures; credit and financial-conditions regimes before equal-weight drawdowns;
     the theme composite (Congress, lobbying, contracts, bills) on theme ETFs;
   - stock level, after step 1: distress alerts (late filings, auditor changes,
     restatements, insider selling clusters, short-interest jumps, weak Altman Z) before
     blowups; durable selection (quality, value, insider buying) over 1-3 years.
4. **Skills and evals**: the validated signals become agent skills over the MCP tools,
   with evals built from historical cases (what was known then, what happened).
