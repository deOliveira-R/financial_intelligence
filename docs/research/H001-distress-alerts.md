# H001: public distress signals precede blowups

Status: **pre-registered, not run** (2026-10-12). Track: investing (exception alerts).

## Claim

Companies about to suffer a permanent loss (bankruptcy, deregistration or delisting that
isn't a takeover) disclose it in pieces months ahead: late filings, auditor changes,
restatements, debt acceleration, exchange deficiency notices, impairments, insiders
selling, a weak balance sheet. A combination of these, each dated when public, flags a
small share of companies that holds a large share of the next year's blowups.

## Mechanism (why the price wouldn't already say it all)

The pieces are scattered across forms and months, mostly soft (a late filing has innocent
causes too), and the holders who would act on them often don't read filings: index funds
can't sell, retail holders rarely look. For this track the price question is secondary
anyway: a low-attention investor needs to *exit before a permanent loss*, and a
bankruptcy is permanent whatever the market priced in at the alert. So the test is
whether alerts identify future blowups, not whether shorting them pays.

## Design

- **Unit:** company-quarter. At each quarter end, every member of the research population
  (population.py) with a 10-K or 20-F in the prior 15 months and a public float of at
  least $75 million in its latest annual report (`dei:EntityPublicFloat`, the accelerated
  filer line: excludes shells and micro-caps whose blowups are noise to this portfolio).
- **Outcome:** a blowup within 12 months of the quarter end:
  - an 8-K item 1.03 (bankruptcy or receivership), or
  - a deregistration (15-12B/G, 15-15D) or exchange delisting (25-NSE) with no takeover
    evidence in the 12 months before it (DEFM14A, SC 14D9, SC TO-T, 8-K items 2.01 or
    5.01): takeovers end a listing well, not badly;
  - ambiguous cases are listed and reviewed by hand, not silently classed.
- **Signals**, each "present" if public within the 12 months before the quarter end:
  - late filing (NT 10-K, NT 10-Q, NT 20-F);
  - 8-K 4.01 auditor change, 4.02 non-reliance (restatement), 2.04 debt acceleration,
    3.01 delisting notice or listing-rule failure, 2.06 material impairment;
  - insider net selling: open-market sales by officers and directors exceeding purchases
    by a set dollar share of float, excluding 10b5-1 plan sales;
  - Altman Z below 1.8 (latest statements public by the quarter end);
  - cash runway under 12 months (cash over trailing negative free cash flow).
- **Baselines:** the unconditional blowup rate; Altman Z alone (the textbook model).
- **Measures:** for each signal and for the combined score, lift (blowup rate when
  flagged over the base rate) with a 95% interval clustered by company, the share of
  company-quarters flagged, and recall (share of blowups flagged beforehand). The
  combined score is a count of signals, or a logistic model fitted on the design period
  only.
- **Periods:** design 2012-01 to 2020-12 (fit and choose here); validation 2021-01 to
  2023-06 (look once at the chosen design); holdout from 2023-07 (outcomes complete for
  quarters through 2025-09), run once after the design is frozen.

## What would count

- **Support:** on validation, an alert flagging at most 2% of company-quarters catches at
  least 30% of the blowups in the next 12 months (lift of 15 or more) and does better
  than Altman Z alone at the same alert rate. Then the holdout decides.
- **Rejection:** lift under 5 at a 2% alert rate, or nothing better than Altman Z.
- Also reported whatever the verdict: which single signals carry the weight, how much
  warning time the alerts give (median days from first alert to blowup), and the
  false-alarm cases, read by hand to see what innocent explanations look like.

## Risks to the test itself

- Takeover vs failure misclassification of deregistrations (checked by hand on a sample).
- Population before mid-2011 is large filers only (XBRL phase-in): the design period
  starts 2012.
- Events come from each member's submissions history; members whose filings stopped
  before 2009 aren't in the population, which doesn't matter from 2012 on.
