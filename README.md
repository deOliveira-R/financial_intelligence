# Financial Intelligence

A backend that combines free financial data sources into one stored, queryable dataset. It aims to cover what Alpha Vantage and similar providers offer. See [docs/data_sources.md](docs/data_sources.md) for the research behind the source choices.

## Design

```
providers ──fetch──► raw/ (every response, gzip, content-addressed) + raw_responses index
                        │ loaders (ingest.py) — same code for live syncs and `rebuild`
                        ▼
normalized   issuers ─┬─ securities ─┬─ ticker_history
                      │              ├─ daily_bars (unadjusted, per source)
                      │              └─ corporate_actions (splits, dividends, per source)
                      ├─ filings ── facts ── concepts
                      └─ fiscal_calendars (derived)
             economic_series ── economic_observations
                        │ derive.py — offline, re-runnable
                        ▼
derived      fact labels, filing period ends, fiscal calendars; adjusted prices, split-adjusted
             fundamentals and Q4 computed on read by the API
bookkeeping  sync_runs (one per CLI command), sync_state (last outcome per item)
```

- **Raw layer.** Every provider response is kept, errors included, without credentials. `fin-intel rebuild <fundamentals|prices|economic|all>` wipes those tables and replays the stored responses through the same loaders, with no network. So a parser fix never costs API quota. A full rebuild reproduces the live-synced database exactly. Responses older than this layer (before 2026-10-01) were never captured. `fin-intel prune-raw` keeps raw storage bounded. It keeps the latest N responses per item for snapshot datasets (SEC company facts, FRED), keeps incremental datasets and ticker lists in full (rebuilds replay all of them), drops old error responses, and never touches anything under 31 days old (rate limits and Tiingo's symbol cap count recent calls).
- **Store first, serve from storage.** The HTTP API only reads the database and never calls providers.
- **Reported vs derived.** Values from providers are never edited after load. Our inferences (fiscal calendars, each fact's `period_type`/`fiscal_year`/`fiscal_period`, each filing's `report_period_end`) are written by `derive.py`, which runs after each fundamentals load. `fin-intel derive` recomputes them for every issuer offline.
- **Fundamentals are normalized.** One row per filing and per concept, with integer foreign keys from facts. This takes about a third of the space of the original flat table and gives the API filings lists and concept labels. Each filing's copy of a value is kept, so data is point-in-time.
- **Two price sources.** Tiingo gives deep per-ticker history. Massive's grouped daily endpoint gives every US stock for one day in one call (free plan: 5 calls/minute, 2 years of history). Bars and corporate actions are stored per `source`; `/prices/{ticker}/daily?source=massive` adjusts Massive bars with Massive's own actions. Massive symbols are mapped to SEC-style tickers (BRK.B → BRK-B, JPMpC → JPM-PC), and symbols we don't know (warrants, units, OTC) are skipped, so run `sync-tickers` first.
- **Prices are stored unadjusted**, with splits and dividends in `corporate_actions`. Adjusted OHLCV is computed on read (standard total-return method; within 0.1% of Tiingo's own series since 2010). A new split or dividend never rewrites history, so incremental syncs never need a full refetch.
- **Fiscal periods come from each fact's own dates.** SEC's `fy`/`fp` describe the filing, not the value, and are sometimes mis-tagged. `periods.py` classifies each fact by duration (`annual`, `quarter`, `half`, `nine_months`, `instant`, `other`). It assigns fiscal year/period from the issuer's fiscal calendar, which is inferred from annual and transition (10-KT) filings. It handles 52/53-week years, fiscal years named after the prior calendar year, and fiscal year end changes (the stub period between regimes is `fiscal_period = "T"`).
- **`/fundamentals` returns consistent series.** Share counts and per-share values filed before a split are restated using `corporate_actions` (needs `sync-prices` for that ticker). Missing Q4s are derived as FY − 9M for monetary flows (`derived: true`). All units are returned unless `unit` is given. `split_adjusted=false`, `fill_q4=false` and `as_reported=true` turn these off.
- **Two security masters.** SEC's ticker list is the authority for companies and CIKs. Massive's reference tickers (`sync-reference`) give every security a type (`CS`, `ETF`, `PFD`, `WARRANT`, `UNIT`, `ADRC`, …), composite and share-class FIGIs, and a primary exchange (MIC). They also add the ~5,500 ETFs, funds and notes SEC doesn't list. `origin` records which list created a security (`sec`, `massive`, `massive-otc`, `tiingo`), and each list only deactivates its own. When SEC lists a ticker, SEC claims that security. On CIK disagreements (e.g. preferreds issued by a subsidiary) SEC's CIK is kept.
- **Ticker changes.** SEC side: a rename (FB → META) keeps the security and its history, and the old symbol still resolves. Massive side: the composite FIGI identifies renames (same FIGI, new symbol) and reuse (same symbol, new FIGI). A reused ticker always gets a new security.
- **SEC owns the symbols it lists.** Massive never renames or releases a security SEC lists. When Massive shows that security's FIGI under another symbol (a temporary `D` suffix after a reverse split, an OTC line, a rename SEC hasn't caught up with), the symbol becomes an alias in its ticker history. FIGI-based renames and reuse apply only to securities Massive created. `fin-intel rebuild market` recomputes securities and market data from raw in minutes, without re-loading fundamentals.
- **Delisted securities (no survivorship bias).** `sync-reference` also imports tickers delisted within our market history (origin `massive-delisted`, `ticker` NULL, old symbol in `ticker_history` through `delisted_on`). Market-wide rows are assigned to the security that held the symbol *on that date* (`SymbolResolver`), so a reused symbol's old history stays with the old company. On 2024-10-04, 4,887 listed stocks traded; without delisted securities we saw 4,162.
- **Portfolio** (`portfolio.py`). Accounts, broker transactions and position snapshots. Tax lots are rebuilt from transactions on every request (FIFO; long-term after more than one year). Harvesting candidates come from taxable accounts, checked against purchases in any account within 30 days. Accounts without history are reported from the broker's snapshot (`basis_source: "broker"`). Replacement ETFs are ranked by tracking error, which keeps leveraged funds out. Fidelity positions import directly (`--format fidelity`).
- **Macro pack with point-in-time history** (`macro.py`). 53 FRED series across energy, inflation, rates, activity, labor, dollar, risk and liquidity, each stored with its full revision history (ALFRED vintages, `economic_vintages`).
- **Research time series** (`timeseries.py`, `indicators.py`). Specs such as `px:SPY|sma:200`, `px:CPER/px:GLD|z:252`, `fred:DGS10-fred:DGS2` or `breadth:pct_above_200d` are evaluated on SPY's trading calendar. Every transform is causal. FRED values are point-in-time by default: each appears on its publication date as first printed (August CPI shows up on its Sep 11 release) and changes when revised.
- **Market breadth** (`breadth.py`). Daily internals for NYSE/Nasdaq/NYSE American common stocks: advancers/decliners, the A/D line, up/down volume, new 52-week highs and lows, and the share of stocks above their 50- and 200-day averages. It's split-adjusted, includes delisted stocks, and is recomputed nightly (~20s).
- **Order independence.** Market-wide feeds are keyed by symbol, so rows for unknown symbols are skipped at load time. When a reference sync discovers new securities, their stored Massive bars and actions are loaded from raw (no network). Rebuilds replay reference data first. Either way the result doesn't depend on the order syncs ran in, and a full rebuild reproduces the live database exactly.
- **Rate limits and quotas** hold across processes: limiters are seeded from recorded calls, and Tiingo's 500-symbols-per-month cap is checked before calling. `QuotaExceededError` (including Tiingo's HTTP-200 plain-text messages) stops a batch and records the skipped items in `sync_runs`.
- **Fetch, then load.** The raw store commits each response in its own transaction, so it survives a failed load. Syncs therefore fetch everything an item needs before writing, so they never hold SQLite's write lock during a fetch. SQLite runs in WAL mode so the API can read during syncs.
- **Database:** SQLite by default. Set `FI_DATABASE_URL` to use PostgreSQL. The schema is managed by Alembic migrations in `src/fin_intel/migrations`, applied automatically on every CLI run.

## Provider priority

| Rank | Provider | Role | Status |
|---|---|---|---|
| 1 | SEC EDGAR | Fundamentals, ticker↔CIK, insiders (Form 4), 13F, filings | ✅ tickers + XBRL facts |
| 2 | FRED / ALFRED | Macro, rates, yield curve, FX, commodities | ✅ series + observations |
| 3 | Tiingo | Adjusted daily price history | ✅ daily bars |
| 4 | Massive (Polygon) | Whole-market daily bars, splits/dividends, reference data | ✅ grouped daily + splits + dividends + reference tickers |
| 5 | Alpaca | Intraday bars, real-time IEX stream | |
| 6 | Finnhub | Real-time quotes, earnings calendar, company news | |
| 7 | yfinance | Fallback prices, options chains | |
| 8 | BLS / BEA / Treasury / EIA | Detailed US macro, yields, energy | |
| 9 | Exchange crypto APIs + CoinGecko | Crypto OHLCV and metadata | |
| 10 | FINRA / CFTC / OpenFIGI / Nasdaq Trader | Short volume, positioning, identifiers, listings | |
| 11 | Stooq | Long global index/FX history | |
| 12 | ECB / IMF / OECD / World Bank | International macro, FX reference rates | |
| 13 | FMP / Twelve Data | Gap-filling (statements, international) | |
| 14 | Alpha Vantage / EODHD | Occasional spot checks only | |

## Setup

Only [uv](https://docs.astral.sh/uv/) is needed. It installs the pinned Python (`.python-version`, currently 3.14) as one of its own managed builds. `pyproject.toml` sets `python-preference = "only-managed"`, so system or Homebrew Pythons are never used. It also creates the project's `.venv` with the locked dependencies:

```sh
uv sync                 # installs Python 3.14 if needed, creates .venv, installs dependencies
cp .env.example .env    # then fill in keys
```

Run everything through `uv run …`; there's no need to activate the venv.

## Usage

```sh
uv run fin-intel sync-tickers                       # ~10k SEC tickers + CIKs
uv run fin-intel sync-fundamentals AAPL MSFT        # SEC XBRL facts
uv run fin-intel sync-prices AAPL MSFT              # Tiingo daily bars (incremental)
uv run fin-intel sync-economic                      # FRED macro pack with revision history
uv run fin-intel sync-reference                     # Massive: ETFs, types, FIGIs (weekly; --otc for OTC)
uv run fin-intel sync-market-daily --since 2026-09-01   # Massive: every US stock, 1 call/day
uv run fin-intel sync-actions --since 2024-10-01    # Massive: market-wide splits and dividends
uv run fin-intel sync-daily                         # scheduled bundle: market bars, actions, breadth, FRED, watchlist
uv run fin-intel timeseries 'px:SPY|sma:200' 'breadth:pct_above_200d' 'fred:T10Y2Y' --csv out.csv
uv run fin-intel derive-breadth                     # recompute market breadth (no network)
uv run fin-intel portfolio import-positions FILE --format fidelity
uv run fin-intel portfolio positions | harvest | realized --year 2026
uv run fin-intel sync-weekly                        # scheduled bundle: security lists, fundamentals, retention
uv run fin-intel serve                              # http://127.0.0.1:8000/docs
uv run fin-intel rebuild fundamentals               # re-load from raw/ after a parser change
uv run fin-intel rebuild market                     # securities, prices, actions (minutes)
uv run fin-intel sync-fundamentals-bulk             # every tracked company from SEC's nightly file
uv run fin-intel screen --preset magic              # or --where 'pe<15' --where 'roic>0.15' --sort -fcf_yield
uv run fin-intel derive                             # recompute fiscal labels after a periods.py change
uv run fin-intel prune-raw --dry-run                # what retention would remove (then without --dry-run)
```

API endpoints:

- `GET /status`: recent sync runs and items whose last sync failed
- `GET /securities?q=&type=ETF&active=true`
- `GET /securities/{ticker}`: includes ticker history
- `GET /prices/{ticker}/daily?start=&end=`: unadjusted and adjusted OHLCV
- `GET /prices/{ticker}/actions`: splits and dividends
- `GET /fundamentals/{ticker}/concepts`
- `GET /fundamentals/{ticker}/filings?form=10-K`
- `GET /fundamentals/{ticker}/calendar`: inferred fiscal year end(s)
- `GET /fundamentals/{ticker}?concept=Revenues&period_type=annual&form=10-K&as_reported=false`
- `GET /economic/{series_id}`
- `GET /economic/{series_id}/observations?start=&end=`
- `GET /timeseries?s=px:SPY|rsi:14&s=fred:T10Y2Y&start=&pit=true&format=json|csv`
- `GET /screener?where=pe<15&where=roic>=0.15&sort=-earnings_yield&preset=magic|deep_value|quality|cash_cows`
- `GET /portfolio/accounts`, `/portfolio/positions`, `/portfolio/lots`, `/portfolio/realized?year=`, `/portfolio/harvest?min_loss=`, `/portfolio/context/{ticker}`, `/portfolio/replacements/{ticker}`

## Deployment

See [deploy/README.md](deploy/README.md): one Oracle Cloud Always Free Arm VM runs the API as a systemd service, scheduled syncs as timers, and nightly backups to object storage. Set `FI_API_KEY` so every endpoint except `/health` requires an `X-API-Key` header.

## Development

```sh
uv run pytest
uv run ruff check . && uv run ruff format .
```

After changing `models.py`, generate a migration and review it before committing:

```sh
uv run fin-intel make-migration "describe the change"   # writes migrations/versions/000N_*.py
```

`tests/test_migrations.py` fails if the models and migrations drift apart.
