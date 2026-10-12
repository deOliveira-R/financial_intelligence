# H001: public distress signals precede blowups

Status: **holdout run once (2026-10-12): the frozen alert holds at 7.3x lift with ~10
months' warning; below the registered bar (partial support). Its edge over a correctly
computed Altman Z'' is smaller than first reported (7.3 against 4.9, intervals touching;
see Errata).** Live as an exception alert on the watchlist and holdings (distress.py).
Track: investing (exception alerts).

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

**2026-10-12, after the validation run (validation now seen once):** validation
(2021-03 to 2022-06) showed a 2.2% base rate and restatements catching 69% of blowups:
the SPAC wave. In April 2021 hundreds of blank-check companies restated for warrant
accounting, and in 2022 many liquidated and stopped reporting, which the outcome counts
as a blowup though a liquidating SPAC returns its trust cash. The first audit had already
shown 91 blank checks among the "failures" and this wasn't acted on. **Blank-check
companies (SIC 6770, as currently classified: SPACs that merged carry their operating
company's code) are excluded.** Design and validation are rerun; because validation has
been examined, it no longer counts as an untouched test: **the holdout is the decisive
one.**

**Without blank checks, 2026-10-12** (insider threshold 0.5% of float; the logit fitted on
design only):

| Alert at 2% | Design 2013-2020 | Validation 2021-03 to 2022-06 (seen once before) |
|---|---|---|
| Base rate | 0.9% (1,037 of 117,000) | 0.7% (151 of 21,316) |
| Logit of all signals | lift 8.0 (6.7-9.7), recall 16.5%, precision 7.1% | lift **7.2** (4.1-10.7), recall 15.9%, precision 5.1% |
| Altman Z'' alone | 3.5 (2.8-4.3), recall 7% | 5.6 (3.2-8.2), recall 11% |
| Median warning | 285 days | 362 days |

Single signals in validation: restatement 5.2, late filing 5.0, short runway 3.3,
exchange notice 3.0, auditor change 3.0, weak Z'' 2.6; insider selling 0.36.

**Reading:** the combined alert keeps its strength out of sample (about 7 times the
base rate, a year ahead) but its edge over Altman Z'' alone isn't distinguishable in
validation, and it stays below the registered bar (lift 15, recall 30%). As an exception
alert for a low-attention portfolio: a flag means "read the filings now" (one in twenty
flagged companies fails within a year, against one in 140), not "sell".

## Holdout (run once, 2026-10-12)

Frozen with the user: all signals in a logit fitted on the design quarters, insider
threshold 0.5% of float. Quarters 2022-09 to 2025-09, outcomes through 2026-09: 46,102
company-quarters, 4,531 companies, a 1.6% base rate (the high-rate years saw more
failures).

| Alert | Flagged | Lift (95%) | Recall | Precision |
|---|---|---|---|---|
| **Frozen logit, top 2%** (ties lift it to 2.9%) | 2.9% | **7.3** (5.8-8.9) | **21%** | 11.4% |
| Altman Z'' alone, top 2% | 2.0% | 3.0 (2.2-3.8) | 6% | 4.7% |
| Count of signals (natural cut) | 3.0% | 6.5 (5.2-7.9) | 19% | 10.2% |

Single signals: debt acceleration 5.9, exchange notice 4.8 (recall 47%), late filing 4.7,
restatement 3.9, impairment 2.7, auditor change 2.6, short runway 2.4, weak Z'' 2.0;
insider selling 0.56. Median warning from the first alert: 314 days.

**Verdict:** across three periods (8.0 design, 7.2 validation, 7.3 holdout) the alert is
stable at about seven times the base rate with roughly ten months' warning, and in the
holdout it beats the textbook model (Altman Z'') by more than twice with non-overlapping
intervals. It misses the registered bar (lift 15, recall 30%): **partial support.** Fit
for use as an exception alert ("read the filings now"), not a sell rule: about one
flagged company in nine fails within a year.

**Next:** run it as a live exception alert on holdings and watchlists; new signals
(going-concern language from 10-K text, short-interest jumps, payment defaults) go into a
new pre-registered hypothesis, since this holdout is spent.

## Errata after the holdout (2026-10-12)

Building the live alert showed Altman Z'' missing for most companies. `annual_snapshots`
keys figures by period end and the alert takes the latest one, which in three quarters out
of four is a 10-Q balance sheet without annual operating income (or annual cash flow), so
Z'' and the cash-runway test came out empty. Z'' was present in only 10-17% of rows.

- **The frozen model is unchanged**: the defect is part of what was tested (its weak-Z''
  and short-runway signals fire only in the quarter after each 10-K), so its three results
  (8.0, 7.2, 7.3) stand for the model as implemented, and the live alert uses exactly it.
- **The benchmark was handicapped**, so the comparison is corrected: Z'' from the latest
  annual statements (`z_annual`, present in 48-61% of rows; the rest lack a tagged input),
  top 2% of rows. Run as its own entry in the trial log (`H001_BENCHMARK`, frozen before its
  single holdout run; studies/h001_benchmark.py):

| Lift at 2% (95%), recall | Design | Validation | Holdout |
|---|---|---|---|
| Frozen alert | 8.0 (6.7-9.7), 16% | 7.2 (4.1-10.7), 16% | **7.3 (5.8-8.9), 21%** |
| Altman Z'' as tested (handicapped) | 3.5, 7% | 5.6, 11% | 3.0, 6% |
| **Altman Z'' from annual statements** | 3.1 (1.8-4.3), 6% | 7.3 (3.4-12.0), 15% | **4.9 (3.1-6.6), 10%** |
| Weak Z'' (annual, < 1.1) as a signal | 2.9, flags 14% | 2.8, flags 21% | 2.8, flags 24% |

In the holdout the frozen alert flagged 2.9% of rows (ties) against the benchmark's 2.0%,
which flatters its recall; its precision (11.4% against 7.6%) and lift are the fairer
comparison. **Corrected reading:** the combined alert beats the textbook model in design
and the holdout on point estimates (by about half in the holdout), ties it in validation,
and the holdout intervals touch: better than Altman Z'' alone, not "more than twice".
The verdict, partial support, is unchanged.

The correct Z'' and runway signals, with going-concern language and short-interest jumps,
belong to a new pre-registered hypothesis (the holdout here is spent).
