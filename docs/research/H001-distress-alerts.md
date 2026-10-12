# H001: public distress signals precede blowups

Status: **design run 2026-10-12 (corrected outcome): between support and rejection;
validation running.** Track: investing (exception alerts).

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

## Amendments (before any results)

**2026-10-12:**
- **Altman's Z'' instead of Z.** The original Z needs the market value of equity, and the
  companies that died have no stored prices. The price-free version (Altman 1995, Z'' =
  6.56 working capital/assets + 3.26 retained earnings/assets + 6.72 EBIT/assets + 1.05
  book equity/liabilities) is used for the signal and the baseline, with its distress
  zone below 1.1.
- **Insider selling threshold** is chosen on the design period among three values
  (net open-market sales by officers and directors over 12 months above 0.5%, 1% or 2% of
  public float); the choice counts as three variants in the trial log.
- **8-K 3.01 is also filed by takeover targets** (the exchange notice when a merger
  closes), so it will raise false alarms on companies that are acquired; it stays a
  signal as registered, and the design reports its precision separately.
- **Periods tightened so no outcome reaches the holdout:** design quarters 2012-03 to
  2020-12; validation quarters 2021-03 to 2022-06 (their 12-month outcomes end by
  2023-06); holdout quarters from 2022-09. As registered, validation quarters through
  2023-06 would have examined blowups up to 2024-06, inside the holdout.
- **Foreign filers mostly drop out:** 20-F filers rarely tag their public float, so the
  $75 million float rule keeps essentially US domestic filers.

**2026-10-12, after the first design run (validation and holdout not examined):** the
hand review of blowups the design called for found the outcome badly misclassified. The
first run counted 3,345 companies as failing in 2013-2021 (a 2.5% quarterly base rate);
in a random sample of 40, most were healthy companies (Ford, Archer-Daniels-Midland,
Hexcel, Allegion, Globe Life) filing a delisting or deregistration for *one class* of
securities (preferred shares, notes, a secondary listing) while they kept reporting, and
SPAC completions. Two corrections:
- an exit form counts only if the company files no periodic report more than 120 days
  after it (it actually stopped existing as a reporting company);
- takeover evidence also includes Form 425 (business combination communications, filed
  under the target), preliminary merger proxies, merger information statements and
  going-private filings (SC 13E3), now stored as events.
The first run's numbers are kept in the trial log as a run on the faulty outcome; they
aren't evidence for or against the claim.

## Results

**Design, corrected outcome, 2026-10-12** (quarters 2013-2020; 117,305 company-quarters,
5,600 companies, 1,055 followed by a blowup within a year: 0.9% base rate; 290 failing
companies, 197 through bankruptcy, 93 stopped reporting). A hand check of 40 found about
four misclassified (a subsidiary's bankruptcy, a SPAC merger, a buyout, a company going
dark): errors that dilute signals rather than flatter them.

| Alert | Flagged | Lift (95%) | Recall | Precision |
|---|---|---|---|---|
| Logit of all signals, top 2% (fitted on these quarters) | 2.0% | **8.0** (6.5-9.4) | 16% | 7.2% |
| Altman Z'' alone, top 2% | 2.0% | 3.6 (2.9-4.2) | 7% | 3.2% |
| Count of signals (natural cut) | 6.5% | 3.8 (3.4-4.4) | 25% | 3.5% |
| Late filing | 4.9% | 4.9 (4.1-5.8) | 24% | 4.4% |
| Restatement (8-K 4.02) | 1.5% | 4.8 (3.3-6.5) | 7% | 4.3% |
| Exchange deficiency notice (8-K 3.01) | 6.8% | 4.6 (4.1-5.1) | 31% | 4.1% |
| Debt acceleration (8-K 2.04) | 1.2% | 3.2 (1.8-4.7) | 4% | 2.8% |
| Weak Altman Z'' | 3.1% | 3.0 (2.5-3.5) | 9% | 2.7% |
| Cash runway under a year | 2.3% | 2.8 (2.2-3.5) | 6% | 2.5% |
| Auditor change (8-K 4.01) | 4.5% | 2.0 (1.4-2.7) | 9% | 1.8% |
| Impairment (8-K 2.06) | 2.1% | 1.7 (0.9-2.6) | 4% | 1.6% |
| Insider net selling > 0.5% of float | 22.5% | **0.27** (0.17-0.37) | 6% | 0.2% |

- The combined alert more than doubles Altman Z'' at the same alert rate and warns a
  median 285 days ahead; it misses the registered bar (lift 15, recall 30%) and clears
  the rejection line (lift 5): in between, with the logit flattered by being fitted on
  these quarters. Validation decides.
- Insider selling marks *healthy* companies (insiders sell more where shares have risen);
  its absence isn't a distress signal by itself, but heavy selling is reassurance.
- Insider threshold chosen by the registered rule (best logit lift at 2%): 0.5% of float.

The first run on the faulty outcome (2.5% base rate) is in the trial log and excluded.
