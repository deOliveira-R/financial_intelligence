# H002: iron tracks present industrial activity, copper anticipates it

Status: **design run 2026-10-12: copper-leads claim not supported; iron coincident
confirmed.** Validation not run (see Results). Track: macro (theses.md, thesis 1). From
the user: steel goes into everything being built now, so iron should mirror the present;
copper carries expectations (wiring for what will be built, traders positioning), so it
should lead.

## Claim

Against US industrial production (and world activity where data allows), iron
indicators are coincident (strongest relation within ±2 months) and copper leads (by 3
to 9 months), and copper adds forecasting power for industrial production once its own
history and iron are known. The *acceleration* of each (the change in its growth rate)
marks turns better than the growth rate itself.

## Mechanism

Steel output and iron ore demand follow current construction and manufacturing orders
being filled. Copper's price includes financial demand from traders and producers who
position for future demand, and copper goes into long-lead projects (grids, plants,
buildings) whose orders come early. If markets anticipate, the price that includes
expectations should move first.

## Data and their traps

- Copper price (IMF via FRED `PCOPPUSDM`, monthly, from 1990 here): a market price.
- Iron ore price (`PIORECRUSDM`): **set by annual benchmark contracts until 2010**, so
  before then the monthly number is a step function with no monthly information. Iron ore
  tests start in 2010.
- US iron and steel production (`IPG3311A2S`, from 1972): a quantity, and a component of
  industrial production itself, so its coincidence is partly by construction. Reported
  with a lag and revised: point-in-time values from ALFRED vintages.
- Steel mill product prices (PPI `WPU1017`, from 1939): the US price of steel.
- Target: industrial production (`INDPRO`), year-on-year growth, final values (what
  actually happened), with predictors point in time.
- China dominates iron ore (over half of world steel): iron ore against US production is
  expected to be weak; that's a finding, not a failure of the claim, which holds for US
  steel output. China's steel output (NBS, World Steel) is a later addition.

## Tests

1. **Lead-lag:** cross-correlation of each indicator's year-on-year growth (and its
   acceleration: the 3-month change in year-on-year growth) with industrial production's
   year-on-year growth, at lags -12 to +12 months; the lag of the peak, with a
   block-bootstrap interval (blocks of 24 months).
2. **Incremental forecasting:** industrial production growth over the next 6 months,
   regressed on its own recent growth (baseline), then adding copper, then iron. Expanding
   window, refit monthly, predictors as known each month; out-of-sample R² against the
   baseline (Clark-West test for nested models).
3. **Market relevance (secondary):** does copper acceleration predict cyclical against
   defensive sectors over the next 3 months (XLI and XLB against XLU and XLP, total
   return, from 1999)?

Variants counted in the trial log: growth or acceleration, three horizons (3, 6, 12
months) for test 2. No other variants.

## Periods

- Copper and steel: design 1990-2012, validation 2013-2023-06, holdout 2023-07 onward.
- Iron ore: design 2010-2017, validation 2018-2023-06, holdout 2023-07 onward.

## What would count

- **Support:** copper's peak lag at least 3 months ahead with an interval excluding 0,
  and iron's peak within ±2 months; copper improves out-of-sample forecasts over the
  baseline in validation (Clark-West p < 0.05) at the 6-month horizon.
- **Rejection:** copper's peak within ±2 months (no lead) or no out-of-sample gain.
- **Mixed outcomes are informative:** if iron ore leads (China's cycle leading the US),
  or acceleration works where growth doesn't, that's recorded as found, not retrofitted
  into the claim.

## Amendments (before any results)

**2026-10-12:** ALFRED vintages for copper, iron ore, steel output and steel prices begin
only in 2015, so strictly point-in-time predictors don't exist for the design period
(the first run returned no values). Predictors are **pseudo-real-time** instead: final
values lagged one month at each month end (each series publishes a month's figure early
the next month). Metal prices are effectively unrevised; US steel output is revised
modestly, the remaining approximation. Industrial production in the baseline stays truly
point in time (vintages since 1927).

## Results

**Design, 2026-10-12** (copper and steel 1990-2012, iron ore 2010-2017; 24 variants,
Bonferroni |t| ≥ 3.08):

| Predictor | Transform | Peak lag (95% interval) | Corr at peak | OOS R² 6m | Clark-West p 6m |
|---|---|---|---|---|---|
| US steel output | growth | **0** (-1 to 5) | 0.69 | -0.16 | 0.85 |
| Copper | growth | 1 (-10 to 7) | 0.43 | -0.02 | 0.53 |
| Copper | acceleration | 7 (-12 to 12) | 0.32 | +0.03 (3m: +0.07) | 0.063 (3m: 0.009) |
| Steel price | growth | -3 (-6 to 12) | 0.44 | -0.02 | 0.23 |
| Iron ore (2010-17) | growth | 10 (0 to 12) | 0.76 | -3.2 | 0.99 |

- **Iron is the present:** US steel output peaks at lag 0, as the user expected (partly by
  construction, being a component of industrial production).
- **Copper doesn't lead in growth terms** (peak at 1 month, interval spanning zero) and
  adds nothing at the registered 6-month horizon: the support criterion isn't met.
- **Copper's acceleration** improves 3-month forecasts (p = 0.009, t ≈ 2.4), below the
  multiplicity bar: a candidate for a fresh pre-registration, not evidence.
- Iron ore's 2010-2017 sample is too short; the large lag with a negative out-of-sample
  R² is overfitting.

Validation isn't run on a design that failed its criterion. Copper acceleration at 3
months was pre-registered on its own as H004 and **rejected** on unseen months
(validation out-of-sample R² -0.03, holdout -0.18).
