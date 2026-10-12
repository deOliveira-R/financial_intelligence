# H005: what copper measures (China's credit, the world's industry, the bond market)

Status: **run 2026-10-12 (design, validation, holdout once): none of the three claims
supported as registered.** Copper is a coincident price of the industrial cycle (the US's
included); the timing signals found are small and don't survive from one period to the
next. Track: macro (theses.md, theses 1 and 2).
From the user, after H004: copper is widely and successfully used to read the economy and
markets, so if it doesn't anticipate US industrial production, what does it represent?

## Claims

Three readings of copper, each tested on its own:

- **(a) China's credit cycle drives copper.** China's credit impulse (the change in new
  credit as a share of GDP) leads copper's price growth, and is known early enough to
  forecast it.
- **(b) Copper speaks for the world, and early.** Copper's growth moves more with China's
  industrial production than with the United States', and because it is priced every day
  while industrial production is published weeks later, it improves *nowcasts* of the
  current month's industrial production (OECD total and US).
- **(c) Copper against gold is a read on the bond market.** The ratio of copper to gold
  (growth and reflation against fear) tracks the 10-year Treasury yield; when the two
  diverge, the gap closes, and the test says which side moves: the yield, the ratio, or
  both.

## Mechanism

China uses over half the world's refined copper, the United States under a tenth. Chinese
stimulus runs through credit, and credit-financed construction and grids use copper.
Copper trades continuously, so it is a timely read of demand that official data confirm
later. Gold rises on fear and falling real rates, copper on growth; their ratio prices
growth and inflation expectations, which are also what long yields price. A divergence
means one market disagrees with the other, and one of them has to give.

## Data

All monthly unless stated; World Bank prices are monthly averages, public about 5 days
after the month ends (commodities.py); none are revised.

- Copper and gold: World Bank Pink Sheet (`wb:copper`, `wb:gold`), from 1960; gold
  floats freely from 1975.
- 10-year Treasury yield: FRED `DGS10`, daily since 1962, averaged by month (matching the
  metals' monthly averages); unrevised.
- China's private credit impulse: BIS (`credit.py`), quarterly from 1987-Q4, known 170
  days after the quarter (credit.PUBLICATION_LAG).
- US industrial production: `INDPRO`, ALFRED vintages (truly point in time).
- OECD total industrial production, growth on a year: `OECDPRINTO01GYSAM`, from 1976;
  no vintages used, so as known = final values lagged 2 months (published 6-8 weeks after
  the month).
- China industrial production, growth on a year: `CHNPRINTO01IXPYM`, 1999 to 2023-11
  (discontinued, so its tests stop in 2023).

## Tests (seven, fixed here; nothing else is tried under H005)

**(a) China's credit and copper**, quarterly (copper as the quarter's average):
- **a1, lead (descriptive of the world, final values):** cross-correlation of China's
  impulse with copper's year-on-year log growth at leads 0 to 6 quarters (the impulse
  first); the lead of the peak, with a block-bootstrap interval (blocks of 8 quarters).
  Support: peak at a lead of 2 quarters or more, interval excluding 0.
- **a2, forecast (usable):** copper's log change over the next 2 quarters, from the
  impulse as known (170 days late), against the historical mean change; expanding window,
  at least 40 quarters of fit. Out-of-sample R² and Clark-West.

**(b) The world and the nowcast**, monthly:
- **b1, whose activity:** the contemporaneous correlation of copper's year-on-year growth
  with China's industrial production growth against that with US industrial production
  growth (final values); the difference with a block-bootstrap interval (blocks of 24
  months). Support: China's correlation higher, interval excluding 0.
- **b2, nowcast of US industrial production:** this month's year-on-year growth (final
  values, the target), nowcast at the month's end from the latest US figure then known
  (ALFRED; the baseline), against the baseline plus copper's growth over the same month
  (known 5 days later: the nowcast is made then). Clark-West.
- **b3, nowcast of OECD industrial production:** the same, with the OECD's latest figure
  known (two months old) as the baseline.

**(c) Copper, gold and the 10-year yield**, monthly:
- The relation: yield = α + β · log(copper / gold), fitted by least squares on all months
  up to each month (expanding, from 1976, at least 60 months). The gap: the yield minus
  the fitted value, known when the month's metal averages are (5 days after the month).
- **c1, the yield closes the gap:** the change in the monthly-average yield from the next
  month to three months after it (from month t+1 to t+4), regressed on the gap (expanding
  window), against no change. Expected sign: negative.
- **c2, the ratio closes the gap:** the same for the log copper/gold ratio, against no
  change. Expected sign: positive (a yield above what the ratio implies is followed by
  the ratio rising).

Forecast tests (a2, b2, b3, c1, c2) report out-of-sample R² against their baseline and
the Clark-West test (one-sided, Newey-West errors for overlapping horizons) and the
average slope sign. **Each is judged at p < 0.007** (Bonferroni over seven), with
out-of-sample R² > 0 and the expected sign where one is stated.

## Periods

| Tests | Design | Validation | Holdout |
|---|---|---|---|
| a (quarters, forecast origin) | 1990-Q1 to 2012-Q4 | 2013-Q1 to 2022-Q4 | 2023-Q3 onward |
| b1 (months) | 1999-01 to 2012-12 | 2013-01 to 2023-06 | not testable (China's series ends 2023-11) |
| b2, b3 (months) | 1980-01 to 2012-12 | 2013-01 to 2023-06 | 2023-07 onward |
| c (forecast origin) | 1981-01 to 2005-12 | 2006-01 to 2023-02 | 2023-07 onward |

Design first (all seven, logged), then validation once, then the holdout once. Every test
is fixed above, so nothing is chosen between the periods: validation and the holdout run
whatever design shows. Validation is decisive; the holdout is short (about three years:
read for direction, confirmed only at the same p).

Robustness, reported but not decisive: b2, b3, c1 and c2 in validation without 2020-03 to
2021-06.

## What would count

Each claim is judged separately on validation (and the holdout where testable):
- **(a) supported** if a1 shows the lead and a2 passes; **"leads but too late to use"**
  if only a1 does (as H003 found for credit reversals).
- **(b) supported** if b1 passes and at least one of b2 and b3 passes.
- **(c) supported** if c1 or c2 passes with the expected sign; which one is the finding
  (the yield following copper/gold, or copper/gold following the yield).
- Failing all: copper's reputation is not supported by these tests, and the honest
  reading is that it is a coincident price of global demand, useful as a description.

## Risks to the test itself

- The copper/gold-yield relation may drift (yields fell for 40 years; gold changed role
  after 2008): an expanding fit is used as registered; a drifting relation will show as
  failure, not be refitted.
- Monthly averages smooth turning points; a daily copper price exists only from 2011
  (CPER), too short for these periods.
- The OECD total includes the United States (about a third), which weakens b3 as a test
  of "the world beyond the US".
- Supply shocks (mine strikes, closures) move copper without demand: noise against every
  claim.

## Results (2026-10-12)

Each forecast test: out-of-sample R² against its baseline, Clark-West p (bar: 0.007).
Design and validation ran on every test, then the holdout once.

| Test | Design | Validation | Holdout (from 2023-07) |
|---|---|---|---|
| a1 China's impulse leads copper (peak lead, 95% interval) | 2 quarters (0-6), fails | **6 quarters (4-6), passes** | 13 quarters, not testable |
| a2 the impulse as known forecasts copper (2 quarters) | R² -0.03, p 0.96 | R² +0.005, p 0.30 | 11 forecasts, not testable |
| b1 copper's correlation: China's industry minus the US's | 0.58 vs 0.59: -0.01 (-0.22 to 0.37) | **0.17 vs 0.46: -0.29 (-0.51 to -0.04)** | not testable |
| b2 copper nowcasts US industrial production | R² -0.007, p 0.67 | **R² +0.04, p 0.002** (0.05, p < 0.001 without the pandemic) | R² -0.11, p 0.96 |
| b3 copper nowcasts OECD industrial production | **R² +0.15, p < 0.001** | R² +0.12, p 0.009 (-0.003, p 0.06 without the pandemic) | R² -0.60, p 0.50 |
| c1 the yield closes the copper/gold gap (expected slope < 0) | R² +0.05, p 0.016, slope < 0 | R² +0.01, p 0.03, slope < 0 | R² -0.11, p 0.51 |
| c2 copper/gold closes the gap (expected slope > 0) | R² -0.04, p 0.81 | R² -0.006, p 0.26 | R² +0.03, p 0.24, slope < 0 |

**Verdicts by the registered rules:**
- **(a) "leads but too late to use", weakly.** In 2013-2022 China's credit impulse ran
  *against* copper at the same time (correlation -0.51) and with it 4-6 quarters later
  (+0.32, at the edge of the leads tested); in 1990-2012 the peak was 2 quarters with an
  interval reaching 0. A lead that moves between periods, and that data published 170
  days late can't exploit (a2 fails in both).
- **(b) not supported.** Copper did *not* move more with China's industry than the US's:
  equally before 2013, and the US's clearly more after. The nowcasts help in one period
  each (OECD in design, US in validation) and neither holds in the holdout.
- **(c) not supported.** The yield tends to close the gap with copper/gold (the right
  sign in design and validation, p 0.016 and 0.03) but the gain is small (R² 0.01-0.05),
  below the bar, and gone in the holdout. Copper/gold doesn't follow the yield.

**Reading (dispassionately):** what holds across periods is that copper's year-on-year
growth moves *with* industrial activity, about 0.5-0.6 correlation with US industrial
production growth, the same as with China's before 2013. That is the real content of
"Dr. Copper": a market price that shows the industrial cycle as it happens, every day,
with no publication lag or revision. It doesn't reliably say what comes next, and its
apparent leads (H002's acceleration, the OECD nowcast, the copper/gold gap) appear in one
period and fade in the next. Practitioners' success stories are consistent with this: a
coincident, timely price looks prescient against data published weeks later, and
episodes where it led are remembered (selection).

**Not tested here, and open:** daily copper (from 2011 only, through CPER) against daily
markets; speculators' positioning in copper (CFTC, already loaded) as the "traders
positioning" part of the user's mechanism; copper as a confirmation filter (agreement of
copper, yields and credit) rather than a predictor alone. Each would be a new
pre-registration.
