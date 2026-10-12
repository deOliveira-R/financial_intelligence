# H004: copper's acceleration anticipates industrial production by a quarter

Status: **rejected (validation and holdout, 2026-10-12): H002's design result was a
product of searching 24 variants.** Track: macro (theses.md, thesis 1; a
follow-up of H002). From H002's design search: copper's *acceleration* (the change in its
growth rate) improved three-month forecasts of US industrial production (Clark-West
p = 0.009, t ≈ 2.4) while copper's growth didn't lead at all. That came out of 24
variants, below the multiplicity bar, so it is a candidate, not evidence; this file tests
it on data H002 never looked at.

## Claim

The acceleration of the copper price, as known at each month end, improves forecasts of
US industrial production growth over the next three months beyond what industrial
production's own recent growth says.

## Mechanism

Copper's price carries expectations (traders and producers positioning for demand, orders
for long-lead projects). A *turn* in those expectations (growth speeding up or slowing
down) should show in the price before it shows in output, while the level of growth
mostly mirrors the cycle already under way (H002: copper's growth is coincident). The
acceleration filters out the part of copper's growth that is already the present.

## Design (one variant, fixed here)

- **Predictor:** copper (IMF via FRED `PCOPPUSDM`, monthly), pseudo-real-time as in H002's
  amendment: at each month end, last month's final value (metal prices are effectively
  unrevised). Year-on-year log growth; acceleration = the 3-month change in that growth.
- **Target:** log growth of industrial production (`INDPRO`, final values) over the next
  3 months.
- **Baseline:** industrial production's own year-on-year growth, as known at the month
  end (ALFRED vintages, truly point in time). **Model:** the baseline plus copper's
  acceleration.
- **Forecasts:** ordinary least squares refitted every month on an expanding window from
  1990, using only months whose 3-month outcome was known by then; at least 60 months
  before the first forecast. Code: studies/h002.py `analyze` (transform "acceleration",
  horizon 3), unchanged.
- **Measures:** out-of-sample R² against the baseline, and the Clark-West test for nested
  models (one-sided, Newey-West errors with 2 lags for the overlapping horizons).
- **Secondary (market relevance):** the same acceleration forecasting the 3-month
  relative total return of cyclical against defensive sectors (XLI and XLB against XLU
  and XLP, equal weights, adjusted closes), against a baseline of the historical mean
  return, expanding window from 1999 (60 months minimum), same measures.

Two tests, each judged at p < 0.025 (Bonferroni over the two). No other variants: no
other horizons, transforms, windows or predictors are tried under H004.

## Periods

- **Validation:** forecasts made at month ends from 2013-01 to 2023-03 (targets end by
  2023-06). H002 used only 1990-2012 for this variant; these months are unseen.
- **Holdout:** forecasts from 2023-07 to the latest month whose target is known (about
  three years, ~36 overlapping forecasts: little power, read for direction). Run once,
  after validation, **whatever validation shows**: the variant is fixed above, so
  nothing is chosen between the two.
- Robustness, reported but not decisive: validation without 2020-03 to 2021-06 (the
  pandemic's collapse and rebound dominate squared errors).

## What would count

- **Support:** validation Clark-West p < 0.025 with out-of-sample R² > 0, and in the
  holdout an out-of-sample R² above 0 (confirmed if its p < 0.025 too).
- **Rejection:** validation out-of-sample R² ≤ 0, or Clark-West p ≥ 0.025.
- **Weak support:** validation passes, the holdout R² is ≤ 0: the gain may be a feature
  of a past regime.
- The secondary test is reported on the same rules; it doesn't decide the primary claim.

## Data not added

China's steel output (the plan's "ideally") isn't on FRED, and the National Bureau of
Statistics and worldsteel would each need a new source; it goes to the backlog as a
predictor for a later hypothesis rather than delaying this one.

## Results (2026-10-12; validation, then the holdout once)

| Test | Period | Forecasts | Out-of-sample R² | Clark-West t (p) | Verdict |
|---|---|---|---|---|---|
| Industrial production, 3 months | validation 2013-01 to 2023-03 | 123 | **-0.028** | -0.59 (0.72) | no gain |
| … without 2020-03 to 2021-06 | validation | 107 | -0.004 | 0.36 (0.36) | no gain |
| … | **holdout** 2023-07 onward | 37 | **-0.183** | -0.88 (0.81) | no gain |
| Cyclicals against defensives, 3 months | validation | 123 | -0.003 | -0.67 (0.75) | no gain |
| … without the pandemic | validation | 107 | -0.002 | -0.55 (0.71) | no gain |
| … | holdout | 37 | +0.009 | 1.01 (0.16) | gain, not significant |

**Rejected.** On months it hadn't seen, copper's acceleration makes industrial production
forecasts slightly *worse* than the baseline, with or without the pandemic, and tells
nothing about cyclical against defensive sectors. H002's p = 0.009 in 1990-2012 was what
one in 24 variants produces by chance, which is why the protocol asks for this test.
(Lead-lag, descriptive: in validation the acceleration's correlation with industrial
production peaks 9 months ahead at 0.30, interval 0 to 12 months: not a usable lead.)

For thesis 1: iron (US steel output) is the present, as the user expected; copper, in
growth or acceleration, hasn't shown it carries the future of US industrial production in
any form tested. Copper's expectation content may be global (China) rather than
American; that would need China's activity data (backlog) and a new hypothesis.
