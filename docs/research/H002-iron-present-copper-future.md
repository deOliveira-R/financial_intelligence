# H002: iron tracks present industrial activity, copper anticipates it

Status: **pre-registered, not run** (2026-10-12). Track: macro (theses.md, thesis 1). From
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
