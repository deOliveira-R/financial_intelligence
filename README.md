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
- **Ticker changes.** A rename (FB → META) keeps the security and its history, and the old symbol still resolves. A reused ticker gets a new security. Delisted securities become `active: false`.
- **Rate limits and quotas** hold across processes: limiters are seeded from recorded calls, and Tiingo's 500-symbols-per-month cap is checked before calling. `QuotaExceededError` (including Tiingo's HTTP-200 plain-text messages) stops a batch and records the skipped items in `sync_runs`.
- **Fetch, then load.** The raw store commits each response in its own transaction, so it survives a failed load. Syncs therefore fetch everything an item needs before writing, so they never hold SQLite's write lock during a fetch. SQLite runs in WAL mode so the API can read during syncs.
- **Database:** SQLite by default. Set `FI_DATABASE_URL` to use PostgreSQL. The schema is managed by Alembic migrations in `src/fin_intel/migrations`, applied automatically on every CLI run.

## Provider priority

| Rank | Provider | Role | Status |
|---|---|---|---|
| 1 | SEC EDGAR | Fundamentals, ticker↔CIK, insiders (Form 4), 13F, filings | ✅ tickers + XBRL facts |
| 2 | FRED / ALFRED | Macro, rates, yield curve, FX, commodities | ✅ series + observations |
| 3 | Tiingo | Adjusted daily price history | ✅ daily bars |
| 4 | Massive (Polygon) | Whole-market daily bars, splits/dividends, reference data | ✅ grouped daily + splits + dividends |
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

The project uses a virtual environment built on the Homebrew Python (`pyproject.toml` sets `python-preference = "only-system"`, so uv never downloads its own interpreter):

```sh
uv venv --python /opt/homebrew/bin/python3.14 .venv   # once
uv sync                                               # install dependencies into .venv
cp .env.example .env                                  # then fill in keys
```

## Usage

```sh
uv run fin-intel sync-tickers                       # ~10k SEC tickers + CIKs
uv run fin-intel sync-fundamentals AAPL MSFT        # SEC XBRL facts
uv run fin-intel sync-prices AAPL MSFT              # Tiingo daily bars (incremental)
uv run fin-intel sync-economic GDP CPIAUCSL DGS10   # FRED series
uv run fin-intel sync-market-daily --since 2026-09-01   # Massive: every US stock, 1 call/day
uv run fin-intel sync-actions --since 2024-10-01    # Massive: market-wide splits and dividends
uv run fin-intel serve                              # http://127.0.0.1:8000/docs
uv run fin-intel rebuild fundamentals               # re-load from raw/ after a parser change
uv run fin-intel derive                             # recompute fiscal labels after a periods.py change
uv run fin-intel prune-raw --dry-run                # what retention would remove (then without --dry-run)
```

API endpoints:

- `GET /status`: recent sync runs and items whose last sync failed
- `GET /securities?q=`
- `GET /securities/{ticker}`: includes ticker history
- `GET /prices/{ticker}/daily?start=&end=`: unadjusted and adjusted OHLCV
- `GET /prices/{ticker}/actions`: splits and dividends
- `GET /fundamentals/{ticker}/concepts`
- `GET /fundamentals/{ticker}/filings?form=10-K`
- `GET /fundamentals/{ticker}/calendar`: inferred fiscal year end(s)
- `GET /fundamentals/{ticker}?concept=Revenues&period_type=annual&form=10-K&as_reported=false`
- `GET /economic/{series_id}`
- `GET /economic/{series_id}/observations?start=&end=`

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
