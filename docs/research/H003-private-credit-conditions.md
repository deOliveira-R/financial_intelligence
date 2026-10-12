# H003: when private credit reversals cause crises (Keen's conditions)

Status: **design run 2026-10-12: rejected as registered; the warning is the boom, not
the reversal.** Conditional result for validation (see Results). Track: macro (theses.md, thesis 3). The
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

## Amendments (before any results)

**2026-10-12, data:**
- Banking crisis dates come from the **Jordà-Schularick-Taylor Macrohistory Database**
  (`crisisJST`, 18 advanced economies) instead of Laeven and Valencia: the IMF site refuses
  automated downloads (HTTP 403, bot protection, which we don't circumvent). Banking
  crises are therefore tested on 18 economies; recessions and drawdowns on the wider BIS
  panel.
- Recessions are the **OECD recession indicators** (FRED `{ISO3}RECM`, monthly, peak to
  trough, through 2022-09) rather than two quarters of falling real GDP: OECD real GDP on
  FRED is missing for most countries. A recession "starts" in the first month the
  indicator turns to 1. The time holdout (2015 onward) therefore ends in 2022.
- **Added: a long-history panel.** The Macrohistory Database also has yearly private
  credit (`tloans`), nominal GDP and equity returns since 1870, with about 90 crisis
  onsets. Test 1 and test 2 are also run there (country-year, credit impulse from yearly
  loans), as an independent second panel; leave-one-country-out applies the same way.
  Success requires the claim to hold in both panels or the difference to be explained.

**2026-10-12, before the time holdout (claims (a) and (b) of Results, frozen):**
- **Feasibility, from outcome counts only:** the Macrohistory crisis dates end in 2020
  and record no crisis onset after 2008. The time holdout (2015 onward) therefore has no
  crises in either panel (the BIS panel borrows these dates): **claim (a) and the crisis
  half of claim (b) can't be tested on time** and stand on the design and the
  out-of-country check only. Recessions aren't part of either claim.
- **What runs, once:** claim (b) for real equity drawdowns of 30% within three years,
  in both panels, on rows from 2015 whose outcome window is covered (BIS: quarters into
  2022-23; Macrohistory: years 2015-2016 only, its equity data ending in 2020). The
  crisis parts and claim (a) are computed too and reported as "no events".
- **Condition (b):** debt percentile ≥ 2/3 of the country's own history, private impulse
  ≥ 0, credit gap > 10 points. The base is every row with the outcome, debt percentile,
  impulse and credit gap known; ratio = rate in the condition over the base rate, 95%
  interval from 1,000 bootstrap draws over countries.
- **Macrohistory credit gap** (not computed in the design, which had none for this panel):
  private loans to GDP minus its one-sided Hodrick-Prescott trend (data up to each year
  only), λ = 400,000 / 4⁴ = 1,562.5 (the Basel quarterly λ of 400,000 scaled
  to yearly data, Ravn-Uhlig), with at least 15 years of history. Its design-period value
  is reported alongside the holdout for reference, not as a test.
- **Verdict rule:** support where the ratio is ≥ 1.5 with the interval's lower bound
  above 1, in both panels; "not decided" where the point is ≥ 1.5 but the interval
  includes 1 (too few rows); rejection where the point is below 1.5.

## Results

**Design, 2026-10-12** (quarters and years before 2015). Ratios are outcome rates over
the base rate, 95% intervals from a bootstrap over countries.

Long-history panel (Macrohistory, 18 economies, yearly since 1870):

| Condition | Crisis within 1 year | Crisis within 3 years | Real drawdown ≥30% within 3 years |
|---|---|---|---|
| High debt, negative impulse | **1.91** (1.54-2.36) | 1.32 (1.11-1.62) | 0.68 (0.57-0.78) |
| … without public offset | **2.35** (1.83-2.92) | 1.50 (1.26-1.82) | 0.72 (0.58-0.87) |
| … with public offset | 0.65 (0-1.81) | 0.90 (0.46-1.34) | 0.58 (0.34-0.79) |
| High debt, positive impulse | 1.01 (0.74-1.36) | 1.35 (1.17-1.58) | **1.31** (1.16-1.46) |
| Low debt, negative impulse | 0.37 (0-0.81) | 0.38 (0.09-0.74) | 0.81 (0.60-1.08) |

Modern quarterly panel (BIS, ~35 economies since the 1990s):

| Condition | Crisis 3y | Drawdown 3y | Recession 1y |
|---|---|---|---|
| High debt, negative impulse | 0.65 (0.45-0.91) | 0.76 (0.66-0.86) | 0.90 (0.82-0.98) |
| High debt, positive impulse | **1.41** (1.21-1.63) | **1.16** (1.09-1.23) | **1.12** (1.07-1.17) |

In the BIS logit with every predictor, the Basel credit gap carries the weight (z 3.4 for
crises, 4.1 for drawdowns, 4.1 for recessions within 3 years); the impulse adds little.

**Reading:**
- Keen's channel (credit reversal at high debt) shows in the long panel for crises within
  a year, and *only without a public offset* (2.35 against 0.65): the user's question
  about the offset is answered in the direction expected.
- But as a *warning* the reversal comes too late: credit contracts as a crisis unfolds,
  and the data arrive about six months after the quarter. In the modern panel the
  reversal isn't followed by more crises, drawdowns or recessions than usual (they have
  begun already); the **boom** (high debt still growing, a wide credit gap) is.
- For equities in both panels, drawdowns follow booms, not reversals.
- The registered criteria (a doubling at 36 months, value beyond the credit gap) fail:
  **rejected as registered.**

**For validation (leave one country out, then the time holdout):** two narrower claims,
pre-registered here before running them: (a) in the long panel, high debt with a
negative impulse and no public offset more than doubles the one-year crisis rate;
(b) in both panels, high debt with a positive impulse and a credit gap above 10 points
raises the three-year crisis and drawdown rates by half or more.

**Validation, leave one country out (BIS panel, quarters before 2015), 2026-10-12:**
the model adding Keen's quantities (debt percentile, impulse, their interaction,
government impulse) to the credit gap, each country predicted from the others:

| Outcome | Events | AUC gap only | AUC with Keen's quantities | Precision at 10% alerts (gap / Keen) |
|---|---|---|---|---|
| Crisis within 1 year | 64 | 0.61 | 0.64 | 0.085 / 0.085 |
| Crisis within 3 years | 184 | 0.60 | **0.68** | 0.27 / **0.30** (base 0.13) |
| Drawdown within 1 year | 244 | 0.56 | 0.58 | 0.17 / 0.13 |
| Drawdown within 3 years | 658 | 0.58 | 0.59 | 0.47 / 0.40 |
| Recession within 1 year | 492 | 0.55 | 0.58 | 0.45 / 0.41 |
| Recession within 3 years | 1,125 | 0.60 | 0.60 | 0.91 / 0.89 |

The debt level and the flow add out-of-country information for **banking crises over
three years** (AUC 0.60 to 0.68, precision 0.27 to 0.30 against a 0.13 base rate) and
little for drawdowns or recessions, where they slightly hurt precision. Predictive power
is modest throughout (AUCs 0.55-0.68): useful as a regime gauge, not a timer.
