# H010: the new best ideas of concentrated, patient managers beat their peers

Status: **pre-registered 2026-10-22, not yet run.** Track: investing, the informed-buyer
trigger from 13F (candidates.md row d). Validation mode A. Runs on H008's price proxy and
machinery.

## Claim

When a concentrated, low-turnover institutional manager opens or substantially adds to a
position that is among their ten largest, the company earns a positive excess return
against size-decile peers over the four quarters after the holdings become public, and
more so when two or more such managers do it in the same quarter. The same signal from
*all* managers (no concentration or turnover screen) is weaker: the screen, not the
disclosure alone, carries the information.

## Mechanism

A manager who holds twenty names and rarely trades has done the research before buying
and expects to be paid over years, not weeks (Cohen, Polk and Silli 2010: managers' largest
active positions outperform; the rest of their book doesn't). Form 13F makes the position
public 45 days after the quarter, buried among 2.4 million rows, and most readers follow a
handful of famous names rather than the pattern. Prices adjust as the thesis plays out,
over quarters, so a late entry still earns part of it. The signal is capacity-limited (the
positions are in mid-caps as often as not) and slow, which is why it survives.

## Design

- **Managers eligible at quarter end q**, by rule and nothing else (no list of names,
  which would be hindsight): a 13F filer with 10 to 40 equity positions at q (shares, not
  options or principal), at least $1 billion of them (the threshold the history keeps),
  holdings reported in each of the four quarters before q, and a turnover at q of at most
  25% (the share of the names held at q-1 no longer held at q).
- **Best idea:** a position among the manager's ten largest by value at q that was not held
  at q-1 or whose shares rose by at least half.
- **Signal per company and quarter:** flagged at q if at least one eligible manager has it
  as a best idea at q (`best_idea`); `consensus` if two or more do; `any_manager` if any
  13F filer in the data (eligible or not) has it as a best idea, the baseline the screen is
  judged against.
- **Entry:** the holdings of quarter q are public by q + 45 days; with quarterly prices,
  the position is taken at the end of quarter q+1 (a conservative lag of up to 90 days) and
  the outcome measured from there: **four quarters** primary (q+1 to q+5), one quarter
  secondary (q+1 to q+2).
- **Prices, universe, peers, controls, exits, screens:** as H008 (quarterly_prices from
  three or more filers, the same CUSIP at both ends, moves beyond +300% screened,
  size-decile peers from total institutional value, sector and momentum controls, exits
  counted).

## Tests

1. **Calendar-time portfolios (decisive):** the `best_idea` portfolio's four-quarter excess
   return over size-decile peers, Newey-West (three lags) mean and one-sided t.
2. **Consensus:** the same for `consensus`.
3. **The screen's worth:** `best_idea` minus `any_manager`, Newey-West.
4. **Beyond the obvious:** the pooled regression with sector and momentum controls, errors
   from a bootstrap over quarters.
5. One-quarter horizon for all of the above, reported.

Variants: the eligibility thresholds (10-40 names, $1B, four quarters, 25% turnover), the
top-ten rule and the half-again increase are fixed here; two signals (`best_idea`,
`consensus`) by two horizons: four logged trials. Bonferroni over four.

## Periods

Eligibility needs four prior quarters and the entry lags a quarter, so signal quarters
begin 2014-Q2 (prices from 2013-Q2):
- **Design:** signal quarters 2014-Q2 to 2017-Q4 (entry 2014-Q3 to 2018-Q1; four-quarter
  outcomes end by 2019-Q1).
- **Validation:** 2018-Q1 to 2021-Q2 (outcomes end by 2022-Q3, before the holdout), once.
- **Holdout:** 2021-Q3 onward, once, after validation whatever it shows (nothing is chosen
  between periods).

## What would count

- **Support:** on validation, the `best_idea` four-quarter excess return is at least two
  points with t ≥ 2 (one-sided), the same sign as in design, positive after controls, and
  larger than `any_manager`'s.
- **Rejection:** excess at or below zero, or no better than `any_manager`.
- **Weak support:** positive but under two points, or vanishing after controls, or no
  better than the unscreened signal (then the disclosure, not the manager, carries it).

## Limits stated in advance

- Only managers above $1 billion are in the history, which is also the eligibility floor:
  the smaller concentrated funds the literature often studies aren't here.
- 13F reports long positions only; a "best idea" could be one leg of a hedge, which the
  turnover and concentration screens reduce but don't remove.
- Fourteen validation quarters with overlapping four-quarter outcomes have little power;
  the result is read with H008's as evidence that accumulates.
