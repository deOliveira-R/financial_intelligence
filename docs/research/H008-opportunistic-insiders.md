# H008: opportunistic insider purchases predict excess returns; routine ones don't

Status: **pre-registered 2026-10-22, not yet run; the price proxy is built and its
coverage measured (see Amendments): two data fixes precede the study.** Track: investing,
the informed-buyer trigger (candidates.md row c). Validation mode A. Prerequisite: the 13F
quarterly price proxy (below).

## Claim

Open-market purchases by officers and directors that are *opportunistic* (the insider has
not bought in the same calendar month in each of the three prior years) are followed by
positive excess returns against size- and sector-matched peers over the next one to four
quarters; *routine* purchases (same month, three years running: compensation and
rebalancing habits) are not. The distinction adds to the plain "insiders bought" signal
that the existing event study measures.

## Mechanism

Insiders know their business; the law lets them trade on judgment, not on material
non-public facts, and their judgment is good. But Form 4s are many (7.3 million rows since
2006), most purchases are habitual or plan-driven, and telling the informed ones apart
needs each insider's own trading history, which few readers assemble. Cohen, Malloy and
Pomorski (2012) found opportunistic trades carry the return predictability and routine
ones none. The underreaction persists because the signal lives mostly in smaller
companies, where institutional capital doesn't bother.

## Data

- Insiders (`insider_transactions`): every Form 3/4/5 since 2006, each issuer, with the
  10b5-1 plan flag; a purchase is `trans_code = P`, `acquired_disposed = A`, not under a
  plan, by an owner whose relationship includes officer or director (10%-owners alone
  are excluded: they are often funds), worth at least $10,000 at the reported price.
- **Routine or opportunistic:** for a purchase in month m of year y by insider i in
  company c: routine if i bought c's shares in month m of each of y-1, y-2 and y-3;
  opportunistic otherwise. Classification needs three prior years, so signals begin in
  2009. An insider with no purchases in the three prior years is opportunistic (as in
  the paper, where unclassifiable insiders are dropped: we report both ways).
- **Public date:** the Form 4's filing date. A company is flagged in quarter q when an
  opportunistic purchase was filed in q.
- **Prices: the 13F quarterly proxy** (`quarterly_prices`, derived from
  `institutional_positions`): for each CUSIP and quarter end, the median of value/shares
  across filers reporting shares (not principal), with the reporting unit (thousands
  before 2023, dollars after) detected per period against daily bars where both exist.
  Mapped to issuers through `cusip_mappings` → `securities.cik`; where an issuer has
  several share classes, the one with the most institutional value. Survivorship-free:
  a company that stops appearing has left the 13Fs. Its accuracy is checked against
  daily bars (median absolute log error, share within 2%) and reported before any test.
- **Outcomes:** total return from quarter end q to q+1 (primary) and to q+4 (secondary),
  from the proxy, split-adjusted (`corporate_actions`), dividends omitted on both the
  stocks and the benchmarks (a small, roughly symmetric understatement). Exits: a
  company without a price at the horizon is handled two ways, both reported: (i) its
  last price (as if sold at the last quarter it was held), and (ii) minus 100% where an
  8-K item 1.03 or a deregistration without takeover evidence (H001's definition) falls
  in the window, the last price otherwise. Takeovers end a listing at roughly the last
  price.
- **Universe and benchmarks:** every research-population member with a proxy price at q
  and q+1. Size: deciles of total institutional value held at q (the same source, no
  gaps). Sector: SIC division. Peers: equal-weight portfolio of the same size decile
  (primary benchmark), and the same sector; momentum control: the stock's prior
  two-quarter return decile.

## Tests

1. **Calendar-time portfolios (decisive):** each quarter, equal-weight portfolios of
   companies flagged opportunistic and, separately, routine, held one quarter; the
   quarterly series of excess returns over the size-decile peers; Newey-West mean and
   one-sided t. The four-quarter version holds overlapping cohorts (Newey-West, three lags).
2. **Routine against opportunistic:** the difference of the two series (same quarters),
   Newey-West.
3. **Beyond the obvious:** a pooled regression of each flagged stock's excess return on
   indicators for size decile, sector and momentum decile, errors from a cluster
   bootstrap by quarter; the intercept is the effect after the obvious.
4. **Against the plain signal:** the same portfolio for *all* qualifying purchases (the
   existing event study's definition); opportunistic must beat it.
5. **Cluster variant:** at least two distinct opportunistic buyers in the quarter.

Variants: the routine definition, the $10,000 floor and the officer-or-director rule are
fixed; two signal definitions (single, cluster) by two horizons: four logged trials, plus
the "unclassifiable dropped" reading. Bonferroni over the total.

## Periods

- **Design:** signal quarters 2009-Q1 to 2017-Q4 (36 quarters).
- **Validation:** 2018-Q1 to 2022-Q2 (18 quarters; four-quarter outcomes end by
  2023-06), once.
- **Holdout:** 2022-Q3 onward, once. From 2024-11 the whole-market daily bars allow a
  monthly-resolution reading of the same signal, reported alongside.

## What would count

- **Support:** on validation, the opportunistic one-quarter portfolio's excess return
  over size peers is at least one percentage point a quarter with t ≥ 2 (one-sided), the
  same sign as in design, larger than the routine portfolio's, and still positive after
  test 3's controls.
- **Rejection:** excess at or below zero, or routine at or above opportunistic.
- **Weak support:** positive but under a point a quarter, or vanishing after controls:
  the signal would be size or momentum in disguise.

## Amendments (2026-10-22, before any result: what the price proxy turned out to cover)

The proxy was built (`quarterly_prices`, 806k CUSIP-quarters, exact against daily bars
where both exist) and its coverage measured before any signal was computed:

- **It begins in 2013-Q2, not 2001.** SEC's 13F data sets start with filings of 2013-Q2;
  earlier quarters hold only the few positions that later amendments restated (4-5k
  CUSIPs a quarter against 12.5k from 2013-06-30). **Periods move:** design 2013-Q3 to
  2018-Q4 (22 quarters), validation 2019-Q1 to 2022-Q2 (14 quarters), holdout 2022-Q3
  onward, plus the monthly reading on whole-market daily bars from 2024-11. Fewer
  quarters than registered: the four-quarter horizon has little power and the one-quarter
  horizon decides.
- **Coverage of the signal's companies is partial and size-biased.** Of company-quarters
  with a qualifying officer or director purchase, those with a proxy price from three or
  more filers: 2013 30%, 2015 38%, 2018 49%, 2020 61%, 2022 70%. Two causes, both to fix
  before the study runs, each a data step rather than a design choice:
  1. **CUSIP to issuer mapping.** Only ~3,000 of ~12,500 CUSIPs a quarter reach a CIK.
     OpenFIGI sometimes answers a CUSIP with a foreign listing first (Exxon's 30231G102
     maps to its Mexican line, EXMOC, and so to no security), and companies that died
     before Massive's delisting list have no security row at all (Celgene maps to the
     right US composite FIGI, which nothing carries). Fixes: store OpenFIGI's share-class
     FIGI (global across listings) in `cusip_mappings` and link by it; for CUSIPs still
     without a security, bridge CUSIP → OpenFIGI's US ticker → the ticker insiders filed
     under in the same window (`insider_transactions.issuer_symbol`, dated) → CIK,
     recorded as such (`cik_source`), never by name.
  2. **Filers kept in history.** Before 2025-06 only filers with at least $1B under
     management are stored (96% of assets, but the small companies insiders buy are held
     by small managers). The proxy should be computed from the raw 13F files over *every*
     filer, which the raw store has; a derive pass over the files rather than a wider
     positions table.
- Only after both fixes is coverage re-measured and written here; the study runs on what
  remains, with the covered share per year reported as a limit of the result.

Small companies, one-quarter holding: about 0.5% round trip is assumed when judging
whether a supported effect is worth trading; a 4%-a-year excess net of that is the floor
for a tilt, not a standalone strategy.

## Risks to the test

- The price proxy is quarterly and from institutional reports: a thinly held company
  has few reporters and a noisy median (reported: the number of filers per price; stocks
  with fewer than three are dropped and the count stated).
- Delisting returns are approximations either way; both readings are reported.
- Classification needs the insider's own history: an insider new to the company is
  "opportunistic" by construction (the paper drops them; both readings reported).
