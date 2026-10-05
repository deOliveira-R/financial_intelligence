# Backlog

Things deliberately deferred, to pick up when they become useful. The phases themselves are
in [roadmap.md](roadmap.md).

## Next: history depth and outcome labels (agreed 2026-10-04)

Breadth is enough for a proof of concept; hill-climbing signals needs depth (full market
cycles) and outcome labels. In order:

1. **Free backfills** (existing loaders, earlier start dates): insider data sets back to
   2006 (`sync-insiders --since 2006q1`), 13F data sets back to 2013 (`sync-13f --files N`),
   congressional reports back to 2012 (`sync-congress --since 2012`), full FRED release-date
   history (`releases.HISTORY_DAYS`), Korea back to 2015 (daily `sync-dart` continues),
   Japan (EDINET backfill to 2024-04 done; EDINET offers ~10 years).
2. **Event labels from EDGAR**: 8-K item codes (earnings dates via item 2.02, M&A, guidance,
   executive changes, bankruptcies, restatements), 13D/13G activist stakes, into an events
   table; plus a forward-outcomes table (excess returns vs sector/equal-weight benchmarks,
   drawdowns, re-ratings) as the target for hill-climbing.
3. **Deep US prices**: survivorship-free history with delistings. Free route: slow Tiingo
   backfill (~500 symbols/month: index members, sector ETFs; survivorship-biased). Paid:
   Norgate (~$300/year) or Sharadar (tens of $/month), the recommended first purchase.
   Then backfill point-in-time metrics to ~2009 (SEC XBRL starts there).
4. **Filing text** for LLM analysis skills: 10-K/10-Q sections (risk factors, MD&A), 8-K
   press releases, proxies (EDGAR, free); EDINET report text. Transcripts are paid.
5. **Domain data**: FINRA short volume/interest history, CBOE put/call statistics, SEC N-PORT
   (ETF holdings → index membership since 2019), World Bank commodity prices, freight-rate
   headlines (cyclicals like ZIM), policy data (bills, lobbying, contracts, committees).
6. Then the dedicated signal-design session: hypotheses, analysis skills, evals,
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
