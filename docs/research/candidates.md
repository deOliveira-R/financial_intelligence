# Candidate hypotheses: where the money would come from (2026-10-22)

The research queue after H001-H005, agreed with the user on 2026-10-22. Each row is a
candidate for pre-registration, not a result; its prior is our guess, before any test, of
the chance a pre-registered test shows an effect that survives costs and the holdout.
Rows are retired here when a hypothesis file takes them over or a test rejects them.

## Sources of return

A strategy earns from one of a few sources, and each is tested and fails differently:

| Source | Paid for | Evidence base |
|---|---|---|
| Risk premia | bearing a risk others shed (equity, term, credit, variance, carry) | strong; the edge is sizing and surviving the tail |
| Behavioral anomalies | others' slow reaction (momentum, PEAD, quality, value) | real but about halved out of sample (McLean-Pontiff 2016); mostly small caps |
| Information aggregation | reading public data better (insiders, 13F, 8-K items, spin-offs, filing text) | mechanism-backed; our data is unusually complete here |
| Forced flows | the other side of a non-economic trade (index deletions, spin-off dumping, tax-loss selling) | strong mechanism, few events |
| Loss avoidance | not holding the blowup (H001, regime filters) | improves compounding more than averages |
| Tax and cost | certain money (harvesting, lot choice) | about 0.5-1% a year, built |

Base rate: most pre-registered hypotheses will be rejected, as H002-H005 were. What
survives is usually a risk premium (real money, the tail is the price) or something
capacity-constrained (small caps, illiquidity, patience): where a one-person account
beats institutions. Its other advantages: long horizons, tax control, point-in-time data.

## The binding constraint: stock-level price history

Checked on the server 2026-10-21: whole-market daily bars, survivorship-free, from
2024-10 only (~30k securities); ~470 deep-history names from 1962-2009 (survivorship-
biased); the quarterly price proxy from 13F values (research.md, step 1) was planned but
never built. Building it gives survivorship-free quarterly prices for every stock since
2001, which unlocks every stock-level row below at 3-24 month horizons. Market- and
industry-level tests have decades of clean data.

## Candidates

| # | Hypothesis (mechanism) | Unit, horizon | Data | Prior | Status |
|---|---|---|---|---|---|
| a | Trend + credit regime filter cuts drawdowns at little return cost (slow institutional deleveraging, volatility clustering) | market, monthly, decades | ready | 0.7 drawdown, 0.4 return-neutral | **H006: rejected** (2026-10-22; worked through 2015, failed every fast drawdown since) |
| b | Distress II: going-concern XBRL tag (since ~2017), short-interest jumps, auditor changes improve on H001 | company-quarter, 12m | ready (check tag coverage in `facts`) | 0.5 | queued |
| c | Opportunistic insider buying (Cohen-Malloy-Pomorski: same-month-every-year insiders are routine, the rest informed) beats size/sector peers | stock, 3-12m | insiders ready; needs the 13F price proxy | 0.6 gross, 0.4 net | **H008** |
| d | 13F best ideas: new top-10 positions of concentrated long-horizon managers, entered 45 days late, beat peers | stock, 12m | 13F ready; needs price proxy | 0.5 | queued |
| e | PEAD without analysts: surprise from a seasonal random walk on XBRL EPS, dated `first_filed` | stock, 60 trading days | 2 years whole market + deep universe since 2009 | 0.5 small, 0.2 large | queued |
| f | Spin-offs (Form 10-12B): spun entities outperform 12-24m after forced selling | ~40 events a year | check Form 10 in `corporate_events`; needs price proxy | 0.5, low power | queued |
| g | Quality (gross profitability) and value composite vs equal-weight universe | stock, 12-36m | `screentest` exists | 0.35 value, 0.5 quality | queued |
| h | Variance risk premium: selling index puts earns more when VIX exceeds realized volatility (insurance demand, dealer constraints) | index, monthly, since 2007 (PUT) | Cboe index CSVs (loaded) | 0.7 premium, 0.4 conditioning | **H007: conditioning rejected; the premium stands, modest** (0.5%/month, Sharpe 0.4-0.6, -33% in 2008) |
| i | VIX futures roll-down in contango is a premium, not a forecast; the tail is the price | VIX futures, monthly | Cboe CFE CSVs, free | 0.6 | queued |
| j | Earnings-move straddles: implied vs realized moves on a watchlist | stock-event | needs chains (below) | unknown | needs data |
| k | Sector and asset time-series momentum (12-1, dual momentum) across ETFs | ETF, monthly, since 1998 | ready; backtester needs rotation | 0.6 trend, 0.4 cross-sectional | queued |
| l | COT positioning extremes, contrarian | commodity ETF, 1-3m | ready since 2006 | 0.3 | queued |
| m | Short interest: aggregate level predicts market returns (Rapach-Ringgenberg-Zhou); high days-to-cover with weak momentum underperforms (a not-buy filter) | market; stock | since 2017-12 only: underpowered at market level | 0.3; 0.5 | queued |
| n | Pre-FOMC drift, release-day effects, turn of month, January small-cap rebound | market, days | ready | 0.3 (faded after publication) | queued |
| o | Theme composite (Congress, lobbying, contracts, bills) on theme ETFs vs equal weight | theme, 6-24m | ready | low power; evidence accumulates | plan.md |
| p | The variance risk premium (VIX minus realized volatility) predicts next-month equity excess returns (Bollerslev-Tauchen-Zhou 2009) | market, monthly, since 1990 | ready (Cboe VIX); H007's descriptive test 5: t = 3.3 (1993-2012), 3.7 (2007-2015), then **-0.7 (2016-2023) and -1.3 (holdout)** | 0.3 (weakened) | queued, low priority |
| q | **Volatility-managed exposure** (Moreira-Muir 2017): equity weight proportional to a target over last month's realized variance, capped, monthly; volatility is persistent while returns aren't lower when it's high, so scaling down in turbulent months raises the Sharpe ratio and cuts drawdowns without a binary exit | market, monthly, SPY since 1993, RSP since 2003 | ready | 0.5 | **H009** (pre-registered 2026-10-22): the regime dial after H006's rejection, judged on H006's periods |

Not testable yet: breadth divergence (whole-market breadth exists for two years; earlier
breadth would be computed on survivors); index deletions (no free bulk list of S&P changes).

## Data to get, by what it unlocks

1. **13F quarterly price proxy** (derive, no new source): c, d, f and long-history g.
2. **Ken French data library** (loaded 2026-10-22, `factors.py`, `ff:` series): the daily
   factors since 1926 (five factors since 1963, momentum), for the "beat the obvious"
   controls in every stock-level test and thesis 5. Industry and size portfolios are a
   file away when a study needs them.
3. **Cboe indices** (loaded 2026-10-22, `volatility.py`; free CSVs at
   `cdn-api.cboe.com/api/global/us_indices/daily_prices/{SYMBOL}_History.csv`): daily
   from VIX 1990, SKEW 1990, BXMD and CNDR 1986, BXM 2002, VVIX 2006, **PUT 2007** (seven
   stray values before: not usable earlier), VIX6M 2008, VIX3M 2009, CLL 2008, VIX9D
   2011. VIX futures (CFE historical CSVs) for i.
4. **Options chains**, three tiers: start recording end-of-day chains for the watchlist
   now (Massive's options tier, if the free plan covers it: check); Alpha Vantage
   `HISTORICAL_OPTIONS` (full chain with IV and Greeks for a symbol-date, 15+ years, 25
   requests a day free) for targeted studies such as j; paid (ORATS, Cboe DataShop) only
   if h-j show promise.
5. Later: Treasury FiscalData auctions (thesis 2), S&P index changes, Indeed and NY Fed
   labor series (theses 7-8).

## How the pieces compose

"Buy or not" isn't one model but a stack, each layer earning its place in a test: a regime
dial (a) sizing total exposure; a not-buy filter (H001, m, b); a tilt (g); an
informed-buyer trigger (c, d, f, e); options as the asymmetric expression of the macro
theses (h-j say when insurance is cheap relative to the credit and concentration regime).
Speculation (i, k, l) runs on its own capital with its own stop. Separate from all of it,
risk *measurement* needs no forecast: factor loadings, what the current mix did in
2000-02, 2008, 2020 and 2022, theme concentration through the graph.

Two things decided in advance: the holdout (2023-07 onward) is one bull regime, so
defensive signals are judged on validation and the expanding-window view, with the
holdout read for the cost of lagging; and with about fifteen hypotheses at p < 0.05 one or
two false positives are expected, which the trial log's count covers.
