# What the backend can do

Organized by the four goals the project serves (see [roadmap.md](roadmap.md)): portfolio
decisions, finding undervalued companies, following big players, and trading indicators.
CLI commands are `uv run fin-intel <command>`; API endpoints need an `X-API-Key` header
on the server. The investing track is meant to be low-attention (durable picks, alerts
only on exceptions), the speculation track high-attention with its own signals.

## 1. Portfolio (tax lots, harvesting)

- Import Fidelity position exports (`portfolio import-positions FILE --format fidelity`);
  accounts, transactions and broker snapshots are stored.
- Tax lots rebuilt from transactions (FIFO, long-term after one year); accounts without
  history are reported from the broker's snapshot.
- Tax-loss harvesting candidates in taxable accounts, checked against purchases in any
  account within 30 days (wash sales), with replacement ETFs ranked by tracking error.
- API: `/portfolio/accounts`, `/positions`, `/lots`, `/realized?year=`, `/harvest`,
  `/context/{ticker}`, `/replacements/{ticker}`.
- Not yet: Fidelity transaction history and Vanguard 401(k) imports (waiting for exports).

## 2. Undervalued companies

- **Fundamentals** for about 9,000 SEC filers (10-K/10-Q/20-F/40-F, US GAAP and IFRS),
  plus Japan (EDINET), Korea (DART), Taiwan (TWSE/TPEx) and Europe (ESEF). 28
  standard line items per period; standalone quarters derived from year-to-date reports.
- **Metrics** daily and point in time (`company_metrics`): market cap, EV, P/E, EV/EBIT,
  EV/EBITDA, P/FCF, P/B, yields, margins, ROE, ROIC, growth, leverage, Piotroski F, Altman
  Z, operating margin vs its 5-year average (cyclical peaks). Foreign financials are
  converted to USD; splits and ADR ratios are accounted for.
- **Valued universe** (October 2026): about 5,000 US-listed companies, about 1,900
  Taiwanese (home prices), and a selected set of liquid Japanese and European companies through
  US OTC lines and ADRs. Korea only through SEC filers' ADRs.
- **Screener:** filters (`pe<15`, `roic>=0.15`…), sort, Greenblatt's magic-formula rank,
  presets (`magic`, `deep_value`, `quality`, `cash_cows`), sector include/exclude (SIC
  divisions; magic excludes financials and utilities). `fin-intel screen`, `/screener`.
- **Index membership:** holdings and weights of index ETFs (IVV for the S&P 500, IJH, IJR,
  Russell, Nasdaq-100, sector SPDRs, SMH/SOXX, ITA, XBI/IBB) from N-PORT since late 2019,
  point in time (by filing date), survivorship-free. `fund-holdings IVV --as-of 2021-03-31`,
  `/funds/{ticker}/holdings`.
- **Screen backtests:** monthly point-in-time snapshots (`backfill-metrics`), then each
  screen's picks vs SPY and vs the universe (means and medians) at 3, 6 and 12 months
  (`screen-backtest`). About two years of history so far.
- API: `/fundamentals/{ticker}` (+ `/statements`, `/filings`, `/concepts`, `/calendar`),
  `/metrics/{ticker}`, `/screener`.

## 3. Big players

- **Insiders:** every Form 3/4/5 transaction (quarterly data sets since 2024 Q4, then each
  day's Form 4s); cluster buys (3+ insiders buying on the open market within 30 days, not
  under 10b5-1 plans). `/insiders/clusters`, `/insiders/{ticker}`.
- **Institutions (13F):** every filer's quarterly holdings, summed per security, with
  quarter-over-quarter changes; CUSIPs mapped to securities via OpenFIGI.
  `/holdings/managers?q=berkshire`, `/holdings/managers/{cik}`, `/holdings/security/{ticker}`.
- **Congress:** House (PDF) and Senate (eFD) periodic transaction reports since 2024:
  member, owner, ticker, type, amount range, dates. `/congress/trades`, `/congress/popular`.
- **Policy activity:** federal contract obligations by industry (NAICS), monthly since
  October 2007 (`gov:3364` in specs: aerospace; `gov:336414`: guided missiles), known 90
  days after the month (DoD's reporting delay). `sync-contracts`.
- **Event studies:** average returns vs SPY after disclosures (insider clusters, congress
  purchases, new 13F positions), from the next trading day. `event-study`, `/events/{source}`.
  Interpreting these signals properly (fair benchmarks, their own unit and horizon) is
  planned as a dedicated piece of work.

## 4. Trading indicators and research

- **Macro pack:** 70 FRED series (energy, inflation, rates and curve, activity, commodities, freight, labor, Japan,
  dollar, credit and volatility, liquidity) plus exchange rates for 21 currencies, all with ALFRED revision
  history for point-in-time use. `/economic/{id}`.
- **Energy:** 13 EIA weekly series (crude, Cushing, gasoline and distillate stocks,
  production, imports/exports, refinery utilization, products supplied, gas storage).
- **Positioning:** CFTC Commitments of Traders for 26 markets since 2006, with a 3-year
  COT index per trader group. `/cot`, `/cot/{market}`.
- **Options sentiment:** Cboe put/call ratios and volumes (equity, index, total since 2006;
  SPX, VIX options, ETPs since 2019), known the next day. `cboe:equity_pc`.
- **Short interest:** shares short and days to cover per stock, twice a month since
  December 2017 (FINRA), known 8 business days after settlement. `si:GME`, `si:GME:dtc`.
- **Market internals:** daily breadth for US common stocks (advancers/decliners, A/D line,
  new highs/lows, share above 50/200-day averages), delisted stocks included.
- **Calendar:** scheduled releases (CPI, payrolls, GDP…), FOMC and Bank of Japan
  decisions. `fin-intel calendar`, `/calendar`.
- **Yen carry trade gauge:** US-Japan differentials (3-month, 2- and 10-year), carry-to-risk,
  MXN/JPY and AUD/JPY, speculators' yen futures positioning, Japanese residents' foreign
  bond purchases (repatriation), next BOJ/FOMC decisions, and flags (yen rally, volatility
  spike, crowding, narrowing differential), point in time. `fin-intel carry`, `/carry`.
  Inputs: the MoF JGB curve since 1974 and weekly flows since 2005 (`jp:jgb10y`,
  `jp:out_bonds`).
- **Time series engine:** specs like `px:SPY|sma:200`, `px:CPER/px:GLD|z:252`,
  `fred:DGS10-fred:DGS2`, `breadth:pct_above_200d`, `cot:gold:managed_money:index`,
  `eia:crude_stocks|diff:1`, `jp:jgb10y`, `gov:3364`, `si:GME:dtc`, `cboe:equity_pc`, with causal transforms (sma, ema, rsi, macd, ret, diff, vol,
  z, high, low, dd, yoy) on SPY's trading calendar. `timeseries`, `/timeseries`.
- **Backtests:** rules on any specs (`px:SPY > px:SPY|sma:200 and breadth:… > 40`),
  decided at each close and held the next day, with costs, long/flat or long/short,
  per-year breakdown. `backtest`, `/backtest`.

## 5. Filing text (for language-model analysis)

- 10-K risk factors and MD&A, 10-Q MD&A and risk factor updates, and earnings press
  releases (8-K item 2.02 exhibits) as plain text, for the deep-history universe since 2023
  (`sync-filing-text`, any company with `--cik`).
- Full-text search with SQLite FTS5 (words, "phrases", OR, NEAR), with snippets:
  `search-filings '"export controls" NEAR china'`, `/filings/search?q=...&ticker=NVDA`;
  full sections at `/filings/{accession}/text`.

## Data operations

- `sync-daily` (Mon–Fri after the US close): market bars, splits and dividends, breadth,
  SEC fundamentals (bulk, changed companies only), insiders, congress, FRED, EIA, Korean
  and Japanese filings, Taiwanese prices, then company metrics.
- `sync-weekly` (Sunday): SEC and Massive reference lists, 13F, CFTC, ADR share counts, SIC
  codes, missing SEC filings (direct XBRL), Taiwanese and European statements,
  cross-listings, release calendar, raw retention.
- `rebuild <fundamentals|prices|market|insiders|holdings|congress|cot|economic|all>`
  reproduces tables from raw without the network; `migrate`, `prune-raw`, `derive*`.
- `/status` shows recent runs and failing items.

## Known limits

- **Prices outside the US:** only Taiwan has home-market prices. Japan and Europe are valued
  only where a US line trades at least 5 days a month (and an ADR's ratio can be inferred).
  Korea isn't valued except through SEC filers. A licensed global feed would fix this (see
  [data_sources.md](data_sources.md#paid-consolidators)).
- **History depth:** Massive's free plan gives two years of whole-market bars. Deep
  history (Tiingo, decades) covers the deep-history universe: about 440 large companies in
  six strategic industries plus 48 benchmark ETFs (`fin-intel universe`,
  `sync-deep-history`). Longer backtests are limited to that universe, and survivorship-
  biased (today's companies).
- **Taiwan income statements** fill in as quarterly snapshots accumulate (the open data is
  year to date, latest quarter only).
- **Data quality to watch:** rare unadjusted reverse splits and relisted bankrupt companies
  in price series; captive finance arms (Toyota, Ford) inflate EV; banks don't fit EV/EBIT
  or ROIC. See [backlog.md](backlog.md).
- **Licensing:** several free feeds are personal-use only. Fine for this private system;
  a commercial product needs licensed data.
