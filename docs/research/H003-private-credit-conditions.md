# H003: when private credit reversals cause crises (Keen's conditions)

Status: **pre-registered, not run** (2026-10-12). Track: macro (theses.md, thesis 3). The
user's framing: not whether Steve Keen is universally right, but when his mechanism holds,
when it doesn't, and what gets an economy out.

## Claim

Across ~45 economies, a reversal of private credit growth (a negative credit impulse)
precedes recessions, banking crises and equity drawdowns *when private debt is high*,
and much less so when the government's borrowing offsets it or private debt is low. The
private impulse adds warning beyond the Basel credit-to-GDP gap.

## Mechanism

Banks create deposits when they lend, so new private credit adds to spending, and a
slowdown in new credit subtracts from it even while debt still grows. The larger the debt
relative to income, the larger the flow of new credit relative to spending, so its
reversal matters more. Government deficits add spending too: a public impulse that
replaces the private one should cushion the shock (sectoral balances).

## Data

- Predictors (BIS, `credit.py`, all point in time: a quarter counts 170 days after its
  end):
  - private credit to GDP (level, and its percentile within the country's own history to
    date);
  - the private credit impulse (change in the year's new credit as % of GDP);
  - the government impulse (same, for government debt): the offset;
  - the credit-to-GDP gap (the Basel indicator, as the baseline to beat);
  - the private debt service ratio (deviation from its own trailing 10-year mean);
  - composition: household against corporate credit.
- Outcomes, each over the next 12 and 36 months:
  - a systemic banking crisis starting (Laeven and Valencia's IMF database, 1970-2017;
    later years from the ESRB crises database for Europe where available);
  - a recession starting (two consecutive quarters of falling real GDP; OECD data via
    FRED);
  - an equity drawdown of 30% or more in real terms (OECD share price indices via FRED).
- Unit: country-quarter. ~45 economies, from when each country's series begins (1960s to
  1990s).

## Tests

1. **Conditional rates:** outcome frequency when private debt is high (top third of the
   country's own history to date) and the private impulse is negative, against the base
   rate; the same with the government impulse offsetting (private + government impulse
   ≥ 0) or not.
2. **Panel model:** logit of each outcome on the predictors and the interaction
   debt level × impulse, with country fixed effects; errors clustered by country and
   by year (crises cluster in time: 2008).
3. **Beyond the Basel gap:** does adding the impulse to a model with the credit gap
   improve out-of-sample prediction (AUC, and precision at a 10% alert rate)?
4. **The ways out:** for each past episode with high debt and a negative impulse, record
   what followed (crisis, slow deleveraging, inflation, public takeover of the debt) and
   what distinguished them. Descriptive, read case by case.

## Validation

- **Leave one country out:** fit on all other countries, predict the held-out one.
- **Time holdout:** quarters from 2015 onward aren't used in design (crises there are
  few; the main check is out-of-country).
- Robustness: excluding 2008 (one global event shouldn't carry the result); advanced and
  emerging economies separately.

## What would count

- **Support:** high debt with a negative impulse at least doubles the 36-month
  probability of a recession or banking crisis against the base rate (interval excluding
  1), the effect shrinks when the government offsets, and the impulse improves
  out-of-country prediction beyond the credit gap.
- **Rejection:** no doubling, or nothing beyond the credit gap.
- **Conditional support** (the likely and most useful result): it holds under specific
  conditions (household debt, floating-rate economies, no public offset): those
  conditions are the finding.
