# Backlog

Things deliberately deferred, to pick up when they become useful. The phases themselves are
in [roadmap.md](roadmap.md).

## Next: history depth and outcome labels (agreed 2026-10-04, updated 2026-10-05)

Breadth is enough for a proof of concept; hill-climbing signals needs depth and outcome
labels. **No data purchases for now:** the system bootstraps on free data and should pay for
itself before any subscription. Status and order:

1. **Free backfills.** Done (2026-10-05): release calendar back to 2000; congressional
   trades since 2012 (59,969 trades; House PTRs start 2014/2015, the 2014-2017 PDF layout
   is parsed; Senate since 2012); insiders back to 2006 (all 75 quarterly data sets, 7.35M
   transactions). Fixed 2026-10-06, rerunning on the server:
   - 13F before 2024: the listing only matched date-range file names, so the quarterly
     files (`2013q2`…`2023q4`) were never offered; one file (2025-06…08) keeps its tables
     in a folder. Both handled; `sync-13f` now retries any period without a successful load.
   - `sync-events`: SEC throttled it at the first request. SEC retries now wait 15 s
     doubling to ~8 minutes (and honor `Retry-After`); filers synced within 6 days are
     skipped, so a stopped run resumes.
   - Korea back to 2015 continues in the daily `sync-dart`; Japan EDINET backfilled to
     2024-04 (EDINET offers ~10 years).
2. **Event labels**: done. `corporate_events` (8-K items, 13D/13G, late filings, delistings,
   tender offers; full history via `sync-events`) and `forward_outcomes` (returns, excess vs
   SPY/universe/sector, drawdowns, re-ratings per metrics snapshot; `derive-outcomes`).
3. **Deep US prices, free and focused**: industries chosen with the user (2026-10-06):
   semiconductors, AI infrastructure and power, energy and critical materials, defense and
   aerospace, healthcare and biotech, industrials and reshoring (shipping left out), plus
   48 benchmark ETFs. `universe.py` picks each industry's largest NYSE/Nasdaq companies
   (SIC codes plus named companies), saved in `src/fin_intel/deep_history.csv` (~490
   symbols); `sync-deep-history` fetches full Tiingo history (50 requests/hour: ~10 hours).
   Survivorship-biased (current listings only), acceptable for a focused universe. Then:
   `backfill-metrics --start 2009-06-01` and `derive-outcomes` to extend point-in-time
   metrics and outcomes back (SEC XBRL starts in 2009; before Massive's two years only the
   universe has prices, so "universe" benchmarks then mean this universe). Paid options
   (Norgate, Sharadar) stay documented, not planned.
4. **Yen carry trade pack**: done (2026-10-06). MoF JGB curve since 1974 and weekly
   portfolio flows since 2005 (`japan.py`, `jp:` timeseries source, `sync-japan`), BOJ
   meeting dates since 2010 in the release calendar, FRED Japan call and 3-month rates, and
   `carry.py` (`fin-intel carry`, `/carry`): differentials, carry-to-risk, MXN/JPY and
   AUD/JPY, speculators' yen positioning, Japanese foreign bond flows, flags. Not free:
   USD/JPY implied vol, cross-currency basis, prime-broker positioning. Flag thresholds are
   judgment calls for the signal-design session to test.
5. **Filing text** for LLM analysis skills: done for 10-K/10-Q risk factors and MD&A and
   earnings press releases, deep-history universe since 2023, FTS5 search (`filing_text.py`).
   Later: earnings call transcripts (not free), proxies (compensation, related parties),
   EDINET/DART report text, older years.
6. **Domain data**. Done (2026-10-06): federal contract obligations by NAICS
   (USAspending, `contracts.py`), FINRA short interest since 2017-12 (`shortinterest.py`),
   Cboe put/call since 2006 (`putcall.py`), index ETF holdings from N-PORT since 2019
   (`funds.py`), IMF commodity prices and freight indexes (FRED macro pack). Next:
   - Policy: lobbying spend by issue area (Senate LDA: needs the user's free key from
     lda.gov/api/register), bills by policy area (congress.gov: free api.data.gov key),
     committee rosters (unitedstates/congress-legislators, no key); company-level contract
     recipients (name matching to issuers, conservative).
   - Shipping rates (container, dry bulk, tankers): no free API found (Drewry, Freightos,
     Baltic indices are licensed); deep-sea freight PPI is the free proxy.
   - Lithium and cobalt prices: not on FRED; miners and LIT as proxies.
7. Then the dedicated signal-design session: hypotheses, analysis skills, evals,
   hill-climbing on the historical data.

## Data

- **EIA key:** the shared DEMO_KEY is limited per IP (fine on the server, exhausted behind
  VPNs). A free personal key (`FI_EIA_API_KEY`) removes the problem.
- **EIA expectations:** inventory *surprises* (vs analyst consensus) are what move prices;
  consensus isn't free, but changes vs the 5-year seasonal average are a usable proxy.
- **More COT markets:** the curated list in `cot.py` covers 26; add any market by its
  CFTC code. Options-combined reports exist too (futures-only for now).
- **Release-day signals:** the calendar is loaded (`fin-intel calendar`); next is studying
  returns and volatility around CPI, payrolls and FOMC days, and flagging positions into them.
- **Longer price history:** rolling Tiingo backfill of the S&P 500 plus major ETFs within
  its 500 symbols/month (about two months); Stooq for long index and FX history.
- **Intraday and real-time:** Alpaca's free IEX feed (minute bars since 2016, live stream).
- **Market structure:** FINRA short volume, SEC fails-to-deliver.
- **ETF holdings:** SEC N-PORT filings.
- **Options:** yfinance or delayed CBOE chains, with Greeks computed ourselves.
- **Sector breadth:** SIC codes are loaded; breadth per sector (% above 200-day) is next.

- **Price outliers:** a few series have unadjusted reverse splits (one shows 117x in a
  year) or stitch a post-bankruptcy listing onto the old one (WW). Flag implausible jumps
  without a matching split, and break series at bankruptcies/relistings, before research
  relies on means.

## Trading strategy prototypes (phase B data)

Each needs a walk-forward backtest on point-in-time data with transaction costs.

- **Breadth divergence:** SPY near highs while % above 200-day falls (e.g. Oct 2026:
  index at highs, 39% of stocks above their 200-day). Does it precede drawdowns?
- **Copper/gold ratio vs yields:** `px:CPER/px:GLD` as a growth signal against `fred:DGS10`.
- **Curve regimes:** `fred:T10Y2Y` and `fred:T10Y3M` inversions and re-steepening vs equity returns.
- **Credit stress:** high-yield spread z-score as a risk-off filter (FRED keeps ~3 years).
- **Inflation surprises:** breakevens (`fred:T5YIE`) and CPI releases vs sector rotation
  (energy, materials vs long-duration growth).
- **Liquidity:** Fed balance sheet minus reverse repo minus TGA vs SPY.
- **Oil shocks:** `fred:DCOILWTICO|ret:21` vs airlines, energy and broad market (with EIA
  inventories once available).
- **COT extremes:** managed money / leveraged funds at a 3-year index above 90 or below
  10 (`cot:copper:managed_money:index`), against forward returns of the matching ETF
  (CPER, USO, GLD, TLT, IWM). Commercials' index as a contrarian confirmation.
- **Trend and momentum:** SMA/EMA crossovers and RSI on sector ETFs, with breadth confirmation.

Run them with `fin-intel backtest ASSET 'rule'` (backtest.py: rules on any timeseries spec,
next-day execution, costs, long/flat or long/short, per-year breakdown). Still to build:
position sizing (e.g. volatility targeting), multi-asset rotation, parameter sweeps with
walk-forward out-of-sample splits, and storing runs for comparison.

## Global coverage (phase E)

- **Prices outside the US:** done for Taiwan (exchange open data) and via US OTC/ADR lines
  for liquid foreign companies; a licensed global feed (EODHD) would cover the rest. Original
  options: Options: US ADRs and OTC
  ADRs already in our Massive bars (most large foreign companies have one; needs a
  home-listing <-> ADR map and ADR ratios), TWSE/TPEx daily closes (open data), Korea's
  public-data portal (free key), J-Quants for Japan (free tier, delayed), or yfinance as a
  fallback. Stooq now sits behind a JavaScript bot check: not used.
- **Cross-listing gaps (crosslist.py):** ambiguous names are skipped, so TSMC isn't merged
  with its SEC filer yet ("Taiwan Semiconductor" also names another TWSE company); match via
  the SEC 20-F's home ticker or LEI instead. A few one-word merges miss when SEC has no
  incorporation country (Equinor, Grifols). ADR ratios break around stock splits unless both
  lines' prices are split-adjusted (Tokyo Electron's OTC line, Sept 2026). Depositary banks'
  DR directories would give ratios for the ~370 linked ADRs still unvalued.
- **Korea prices:** no free official route (data.go.kr needs a Korean phone, KRX Open API
  registration failed); OTC lines barely trade (Samsung's SSNLF), so Korean companies stay
  unvalued unless they file with the SEC (KB, POSCO, Shinhan, SK Telecom...).
- **Taiwan history:** the open data has only the latest quarter; backfill earlier quarters
  from MOPS so income statements (year to date) can be split into quarters and TTM.
- **Japanese mappings:** refine for IFRS filers with other concept names (Panasonic's debt,
  Kawasaki Heavy's operating profit) and banks/insurers; non-consolidated-only filers.
- **China and Hong Kong:** HKEX and CNINFO filings (mostly PDFs).

## Fundamentals and screening

- **Captive finance arms:** Toyota (and Ford, GM, Caterpillar...) consolidate lending
  subsidiaries whose debt inflates EV and deflates ROIC (Toyota EV/EBIT 24 at P/E 11).
  Exclude financial-services debt (segment data) or flag these issuers.
- **Net-cash companies:** EV/EBIT goes negative when cash exceeds market cap (some Chinese
  ADRs); screens should treat that explicitly rather than rank it as cheapest.
- **ADR ratios:** an ADR can represent several ordinary shares (or a fraction), while
  financials report per ordinary share, so ADR market caps can be off by that ratio. Fix
  with each ADR's ratio (Massive ticker details or depositary filings). Financials in
  currencies without a FRED rate (ARS, ILS, TRY, COP...) get no valuation metrics.
- **Multi-class share counts:** companies reporting EPS and shares per class (Berkshire,
  Greif) get no market cap. Per-class counts are dimensioned XBRL facts, which SEC's
  company facts omit; the filings' instance documents have them.
- **Bank metrics:** price to tangible book, net interest margin, efficiency ratio (banks
  report no operating income, so EV/EBIT and ROIC don't apply).
- **Historical point-in-time metrics:** backfill company_metrics for past dates from
  statement filing dates and historical prices, for screening backtests.
- **Peer-relative valuation:** SIC codes are loaded (`sectors.py` groups them by SIC
  division); next is comparing each company with its industry's median multiples.
- **PostgreSQL:** SQLite holds the full fundamentals load (~20-25 GB, read-mostly, one
  writer); move when concurrency or size demands it.

## Big players (phase D)

- **Paper congressional reports:** about 12% of House PTRs (DocID 8/9) and some Senate
  reports are scanned images; they're indexed but have no transactions. OCR would recover
  them.
- **Congress history:** PTRs go back to 2012; the default load is 2 years. Amendments
  arrive as new reports, so an amended trade can appear twice.
- **Congress data quality:** a few filers mistype dates (7 of 12.8k trades are dated after
  their own report, e.g. 2026-12-26 for a January trade); the notification date could
  stand in. Late disclosures (trades years before the report) are genuine.
- **Policy corroboration (with congress by industry):** committee rosters
  (unitedstates/congress-legislators, no key), bills and policy areas (congress.gov API,
  free key), lobbying spend by industry (Senate LDA API), federal contracts by company
  (USAspending API). Rising lobbying, contracts, bills and member buying in one industry
  is the stronger story.
- **Congress signals:** purchases by several members, committee membership vs sector
  (e.g. Armed Services and defense stocks), excess returns after disclosure date.
- **13D/13G stakes:** activists crossing 5% (EDGAR full-text search or daily index).
- **Famous-investor watchlist:** named 13F filers (Berkshire, Pershing Square, Scion,
  Baupost, Appaloosa...) with alerts on new positions.
- **Signal studies:** `fin-intel event-study insiders|congress|13f` measures returns vs
  SPY after disclosures. Next: control for size and sector, condition on value metrics
  (insider buying in cheap stocks), and use historical tickers for renamed companies.

## Portfolio

- Fidelity transaction-history parser (holding periods, recent purchases, realized gains).
- Vanguard 401(k) import (export or statement).
- Specific-lot identification from Fidelity's lot-level cost basis, when exported.

## Operations

- fail2ban on the server (SSH must stay public for the tunnel).
- macOS launch agent keeping the SSH tunnel up.
- Pin the exact Python patch (`.python-version` 3.14.x) so local and server match.
- Restart the editor's Pyright after the `pyrightconfig.json` change, if warnings persist.
