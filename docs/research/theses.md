# The user's theses (2026-10-12) and how each gets tested

Eight theses from the user, translated into mechanisms, indicators, data and a validation
mode. They form one picture: a cycle layer (credit, sovereign bonds, the AI investment
boom, market concentration), a real-activity layer (industrial metals), and a structural
layer (drones, robots, AI replacing human work over the next decade).

## Three ways to validate

Not every thesis can be backtested, and pretending otherwise is how hindsight sneaks in.

- **A. Predictive indicators with many past episodes:** tested statistically per the
  protocol (research.md): pre-registered, point in time, holdout. Breadth of history
  matters more than precision: a cross-country panel gives dozens of credit cycles where
  the US alone gives three.
- **B. Regime indicators with few past episodes** (sovereign stress, investment manias):
  too few cases for statistics. Validated by historical analogs (what the indicators read
  before 1873, 1929, 2000, Italy 2011, the UK gilt crisis of 2022), by the mechanism, and
  by stating thresholds and what would prove the thesis wrong *before* watching.
- **C. Structural theses about a future without precedent** (drones, robots, AI): no
  history to fit. Built as thesis trackers: measurable milestones and leading indicators,
  investable expressions, and kill criteria. Foresight here means seeing the inflection
  early and knowing what would disconfirm it.

**Bias guard:** most of these theses lean bearish on the cycle. Each tracker carries its
disconfirming evidence (what the bull case would look like) and is reviewed against it,
so that a coherent story doesn't filter what we see. The user's own words (2026-10-12): if
everything looked fine they wouldn't be researching it; they hold a bad view of the
current situation and want to navigate it competently. The research serves navigation:
when the danger is real, how big, how soon, and what the ways out are.

## 1. Industrial metals as the industrial pulse (mode A)

- **Mechanism:** steel goes into construction, machinery, vehicles and ships; demand for
  its inputs moves before reported output. The *change* in consumption (acceleration)
  marks heating up or cooling down.
- **Caveat that shapes the test:** China makes over half the world's steel, so iron ore
  mostly measures Chinese construction and property, not US industry. Separate the two:
  iron ore (global and China) versus US raw steel output and US iron and steel production.
- **Indicators:** iron ore price (IMF, FRED PIORECRUSDM), US iron and steel production
  index (FRED), steel PPI, copper (the classic "Dr. Copper", as the comparison), China
  crude steel output (National Bureau of Statistics; World Steel Association), all as
  levels, year-on-year change and its change (acceleration).
- **The user's refinement (2026-10-12): iron is coincident, copper anticipatory.** Steel
  goes into everything being built now, so iron should track *present* activity; copper
  prices carry more financial and expectational demand (wiring for what will be built,
  traders positioning), so copper should *lead*. This is the hypothesis to test: the
  lead-lag structure of each against industrial production (cross-correlation by lag,
  and whether one adds forecasting power once the other is known).
- **Tests:** the lead-lag structure above; whether either predicts the return of cyclical
  against defensive sectors (equal-weight sector portfolios, Ken French industry data
  back to 1926) beyond what industrial production itself already says.

## 2. Sovereign bonds, trust in the currency, and the trap (mode B, parts A)

- **Mechanism:** when trust in a government's debt falls, buyers demand more yield; high
  rates choke private credit and raise the government's interest bill, which needs more
  borrowing. When debt is high enough, the central bank can't fix both: raising rates
  deepens the fiscal hole, cutting them feeds inflation and weakens the currency (fiscal
  dominance: control is lost because no rate setting works).
- **Inflation and yield-curve control (the user's addition, 2026-10-12):** a bond pays in
  the issuer's currency, so what matters is the *real* return. A government that inflates
  faster than it pays makes its bonds a poor deal against real assets or other
  currencies, and buyers demand more or leave. Yield-curve control (the central bank
  capping long yields by buying bonds: US 1942-1951, Japan 2016-2024, Australia
  2020-2021) and financial repression (real yields held negative so inflation erodes the
  debt: the US 1945-1980 path) are how governments have escaped the trap, at savers'
  expense.
- **The ways out, as outcomes to watch for:** growth outpacing interest (the benign
  case), financial repression (negative real yields for years), yield-curve control,
  austerity, restructuring or default, a currency crisis. Each has indicator signatures;
  knowing which path is being taken matters for positioning (repression favors real
  assets over bonds; austerity hurts equities first).
- **Indicators, each with what normal looks like:**
  - real yields (10-year TIPS, FRED DFII10) and breakeven inflation (FRED T10YIE): real
    yields negative while inflation runs above target is repression;
  - central bank holdings of government debt (Fed balance sheet, FRED WALCL and
    TREAST) against issuance;
  - term premium (the extra yield for lending long; NY Fed ACM, FRED Kim-Wright): rising
    while the Fed cuts is the warning;
  - long yields rising while short rates fall (bear steepening from the long end);
  - **the dollar falling while US yields rise**: in normal times higher yields strengthen
    a currency; the sign flip is how emerging markets look in a confidence crisis
    (UK 2022, Italy 2011);
  - gold rising despite high real yields (it normally moves against them);
  - federal interest outlays over tax receipts (FRED, Treasury FiscalData);
  - Treasury auction demand: bid-to-cover and the foreign (indirect) bidders' share
    (FiscalData), foreign holdings (TIC);
  - debt to GDP, the deficit at full employment.
- **Tests:** the rolling correlation of the dollar with yields, and the gold and real
  yield relation, as regime flags, checked against past episodes across countries (UK,
  Italy, Japan, US 1970s) for false alarms and lead time.

## 3. Private credit and employment before crashes: Steve Keen (mode A)

- **Mechanism (Keen, building on Minsky):** spending is income plus new borrowing, so
  when private debt is high its *growth rate* drives demand. The credit impulse (the
  change in credit growth, as a share of GDP) turning negative takes demand away
  abruptly; crises follow high private debt to GDP plus a falling credit impulse.
  Employment follows with a lag; unemployment rising from its low (the Sahm rule) marks
  recessions in real time.
- **Data:** BIS credit to the private non-financial sector and debt service ratios (free,
  quarterly, about 40 countries for decades): a panel with dozens of crises, which gives
  statistical power the US alone can't. US detail from the Fed's Financial Accounts (Z.1,
  on FRED), bank credit (H.8, weekly), unemployment and claims.
- **The question (the user's framing, 2026-10-12):** not whether Keen is universally right,
  but *when* his mechanism holds and when it doesn't, and what gets an economy out of the
  trap. His lasting contribution is attention to private credit: banks create money when
  they lend (the Bank of England confirmed this in 2014, "Money creation in the modern
  economy"), not by lending out deposits, so private credit growth is a demand source in
  its own right and its reversal a demand shock.
- **Conditions to test:**
  - level: private debt to GDP and the debt service ratio (high debt makes the flow of
    new credit matter more);
  - flow: the credit impulse turning negative;
  - the offset: government deficits can replace private borrowing (sectoral balances:
    private deleveraging offset by public borrowing, as in Japan after 1990 and China
    after 2015, may be why some predicted crashes never came);
  - the external channel: export surpluses also offset;
  - the composition: credit for asset purchases (property) versus for production.
- **Ways out, as outcomes:** a slow deleveraging with growth, inflation eroding debt,
  restructuring, public debt taking over private (moving the problem to thesis 2), or a
  debt jubilee (Keen's proposal). The panel shows which happened in each past episode.
- **Tests:** across countries, do high private debt and a negative credit impulse precede
  banking crises and equity drawdowns, and does a large public offset prevent it? With
  false-alarm rates and lead times. Implementing his full model (Minsky) is a later
  option: it's a stylized simulation, not a forecaster; the conditions above are what's
  testable.

## 4. The AI investment boom unwinds by over-stretch (mode B)

- **Mechanism:** booms rightly form around transformative technologies (railways,
  electricity, fiber), but investors over-extend; the technology arrives later than the
  payback needs, credit tightens, the investment collapses. AI capex is large enough to
  compete with Treasuries for capital, worsening the bond problem (thesis 2).
- **Indicators:**
  - hyperscalers' capex against operating cash flow (company filings, quarterly; we have
    these): capex above internally generated cash means borrowing;
  - debt raised for AI: bonds, private credit, off-balance-sheet data-center vehicles
    (8-K item 2.03, filing text);
  - circular financing: suppliers investing in their customers (filing text);
  - AI revenue against AI capex; depreciation lives of servers (10-K text: lengthening
    them flatters earnings);
  - GPU rental prices, data-center power contracts;
  - equal-weight against cap-weight (thesis 5).
- **Analog:** telecom and fiber 1996-2002: capex far beyond revenue, vendor financing
  (Lucent, Nortel lending to customers), then credit tightened. The tracker states which
  indicator readings matched 2000 and checks today's against them.
- **Disconfirming:** AI revenue growth catching up with capex, capex funded from cash
  flow, productivity data turning up (thesis 8).

## 5. A market carried by seven companies (mode A over long horizons, mode B for timing)

- **Mechanism:** a cap-weighted index concentrated in a few companies inherits their
  risk; equal-weight shows how the typical company is doing.
- **What history says, to test rather than assume:** concentration can persist for years
  (1972 Nifty Fifty, 1999), so it times poorly; over 5-10 years extreme concentration has
  tended to reverse. Breadth deteriorating together with credit (theses 3, 4) is the
  sharper combination.
- **Data:** Ken French's data library (free: size, value and industry portfolios since
  1926, survivorship-free) for long history; SPY against RSP (equal weight) since 2003;
  our breadth series.
- **The "toxic everywhere" counterpoint:** a relative world limits capital flight (where
  would it go?). Track it through US against foreign equities and the dollar.

## 6. Warfare shifts to drones (mode C)

- **Thesis:** drones and counter-drone systems take budget share from conventional
  platforms; drone-native entrants grow faster than primes that don't adapt.
- **Data:** ACLED (the user registered, 2026-10-12: curated political violence and protest
  events worldwide, by type, actor and location, including drone and air strikes) and
  GDELT (multilingual news events).
- **Indicators:** federal contract obligations for unmanned systems (USAspending by
  keyword and product code: we have contracts by NAICS, add text), defense budget lines
  (Replicator), bills and presidential documents on unmanned and counter-UAS (we have),
  revenue growth of drone companies against primes (filings), filing-text mentions,
  multilingual conflict and production news (GDELT: free, multilingual, no account).
- **Frame (the user's):** conflict is the historical norm and peace the exception, so
  the informative quantity is whether the preconditions for peace are holding or
  retracting: trade interdependence, alliance commitments, deterrence balance, economic
  stress, institutional reach. Track their direction, not only conflict counts.
- **Expression and kill criteria:** a drone basket against the primes; the thesis weakens
  if procurement stays platform-heavy or primes win the drone contracts themselves.

## 7. Robots and the entry-level job market (mode C)

- **Thesis:** AI moves into robots within the decade; entry-level positions are where
  substitution shows first.
- **Indicators:** unemployment of recent college graduates against all workers (NY Fed),
  unemployment for ages 20-24 (FRED), job openings by industry (JOLTS), job postings by
  occupation (Indeed Hiring Lab, free), imports of industrial robots (Census trade data by
  HS code), revenue per employee for adopters, filing-text mentions of automation and
  headcount.

## 8. AI cheapens work; humans become the bottleneck (mode C)

- **Thesis:** AI gets far cheaper and faster (models in silicon); after a crash, the
  economy splits: activity that removes humans from the loop takes off.
- **Indicators:** productivity (output per hour, FRED) accelerating while hours stay flat,
  the labor share of income, profit margins against wages, revenue per employee across
  companies, software and equipment investment against structures, AI price per unit of
  work.
- **Note:** this is the bull case for thesis 4: if productivity turns up early enough,
  the boom pays off without the unwind. Trackers 4 and 8 are read together.

## Order of work

1. Mode A first, cheapest and most informative: thesis 3 (BIS credit panel) and thesis 1
   (metals against the industrial cycle), then thesis 5 over long horizons (Ken French).
2. Mode B trackers for theses 2 and 4, with analog readings and thresholds.
3. Mode C trackers for theses 6-8, mostly from data already stored plus GDELT, Indeed and
   NY Fed labor series.

New free sources this needs: BIS statistics, Ken French data library, Treasury FiscalData,
NY Fed (term premium, recent graduates), Indeed Hiring Lab, Census trade, GDELT, World
Steel Association.
