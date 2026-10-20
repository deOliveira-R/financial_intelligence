# Architecture

How the backend is put together and why. For what it can do, see
[capabilities.md](capabilities.md). For where the data comes from, see
[data_sources.md](data_sources.md).

## The pipeline

```
providers/*.py ── fetch ──► raw store: data/raw/<provider>/<hh>/<sha256>.gz + raw_responses index
  rate limits, retries,          (every response, errors included, credentials stripped)
  raw capture                     │
                                  │ loaders declared per dataset (registry.py)
                                  │ (the same code for live syncs and for `rebuild`)
                                  ▼
normalized tables   issuers, securities, ticker_history, daily_bars, corporate_actions,
                    filings, facts, concepts, economic_*, insider_transactions,
                    institutional_*, congress_*, cot_positions, portfolio tables
                                  │ derive.py / statements.py / metrics.py / breadth.py
                                  ▼
derived tables      fiscal_calendars + fact labels, statement_items, company_metrics
                    (point in time, one row per company per day), market_breadth
                                  │
                                  ▼
research            timeseries.py (specs on a trading calendar), backtest.py, screener.py,
                    screentest.py, events.py, portfolio.py
                                  │
                                  ▼
interfaces          cli/ (typer, by area: syncs, rebuilds, research)   api/ (FastAPI routers, read-only)
```

Rules that hold everywhere:

- **Store first, serve from storage.** The API only reads the database. It never calls a
  provider.
- **Raw first.** Every provider response is written to the raw store before it's
  interpreted, so a parser fix never costs quota. `fin-intel rebuild <target>` wipes a set
  of tables and replays the stored responses through the same loaders, without the network.
  A full rebuild reproduces the live database.
- **Fetch, then load.** The raw store commits each response in its own transaction. Syncs
  never hold a write transaction across a network call: they fetch everything an item needs
  before writing, and commit
  per item (per company, per day). A long write transaction blocks every other job.
- **Reported vs derived.** Values from providers are never edited after load. Our
  inferences (fiscal labels, statements, metrics, cross-listings) live in separate tables or
  columns and can be recomputed offline.

## Modules

| Area | Modules | Notes |
|---|---|---|
| Plumbing | `config.py`, `db.py`, `raw.py`, `models.py`, `migrations/` | Settings from `.env` (`FI_*`); PostgreSQL 16 (BIGINT ids, generated tsvector columns for text search); Alembic migrations from a 0001 baseline, run by every CLI command |
| Providers | `providers/base.py`, `providers/ratelimit.py` + one file per source | Rate limits are seeded from recorded calls, so they hold across processes. `QuotaExceededError` stops a batch; `NotFoundError` on 404 |
| Loading | `registry.py`, `sources.py`, `pipeline.py`, `rebuild.py` | Each domain module declares its datasets (`Dataset`: loader, payload json/bytes/request, snapshot, reference, symbol-keyed, rebuild targets) and loads and syncs them; `pipeline.tracked` records each sync item; `rebuild.TABLES` lists what each target wipes |
| Identity | `symbols.py` (`SymbolResolver`), `world.py`, `crosslist.py` | See [Identity](#identity) |
| Fundamentals | `periods.py`, `derive.py`, `statements.py`, `xbrl.py`, `fundamentals.py` | Fiscal calendars from annual reports; 28 standard line items mapped from US GAAP, IFRS, J-GAAP and others |
| Non-US filings | `dart.py` (Korea), `edinet.py` (Japan), `taiwan.py`, `esef.py` (Europe), `world.py` | Each turns its regulator's format into companyfacts-shaped facts |
| Metrics and screens | `metrics.py`, `fx.py`, `sectors.py`, `screener.py`, `screentest.py` | Point in time; foreign financials converted to USD |
| Markets and macro | `prices.py`, `breadth.py`, `macro.py`, `energy.py`, `cot.py`, `releases.py`, `indicators.py`, `timeseries.py` | Prices stored unadjusted; adjustment computed on read |
| Big players | `insiders.py`, `thirteenf.py`, `congress.py`, `events.py` | Forms 3/4/5, 13F, congressional PTRs, event studies |
| Portfolio | `portfolio.py`, `importers.py` | Tax lots (FIFO), harvesting, wash-sale window, replacements |
| Domain data | `japan.py`, `carry.py`, `contracts.py`, `shortinterest.py`, `putcall.py`, `funds.py`, `filing_text.py`, `universe.py` | JGB curve and flows, yen carry gauge, federal contracts, short interest, put/call, index ETF holdings, filing text (tsvector search), deep-history universe |
| Taxonomy and graph | `themes.py`, `industries.py`, `graph.py` | Themes map each industry to every source's codes; BEA input-output tables give industry-to-industry flows; `graph_nodes`/`graph_edges` (derived) hold industries, final demand and themes with `supplies`, `part_of` and `maps_to` edges, walked with recursive CTEs |
| Catalog | `catalog.py` | Feed freshness from `sync_state` against expected ages (`EXPECTED`, mirroring the schedules), failing-item thresholds, tables per feed; `catalog --check` ends sync-daily |
| Dossiers | `dossier.py` | A company or theme on a date, assembled from the other modules, every section point in time and failing independently |
| Timeline | `timeline.py` | Public-dated events read from the source tables on request (no copy): sources register with `@timeline.source("kind")` and filter by companies or a theme (themes' keywords make text matches precise) |
| Research | `backtest.py`, `screentest.py`, `events.py` | Rules on point-in-time series, screen backtests, disclosure event studies |

## Identity

- **Issuers** are companies. SEC filers are keyed by CIK (below 10^10). Companies from
  other regulators get an id in a reserved range derived from their own identifier, so
  ids are stable across rebuilds (`world.issuer_id`):

  | Source | Range | Example |
  |---|---|---|
  | SEC | CIK < 10^10 | TSMC 1046179 |
  | EDINET (Japan) | 1×10^10 + EDINET code digits | Shin-Etsu E00776 → 10000000776 |
  | DART (Korea) | 2×10^10 + corp code | Samsung 00126380 → 20000126380 |
  | TWSE/TPEx (Taiwan) | 3×10^10 + stock code | TSMC 2330 → 30000002330 |
  | ESEF (Europe) | 10^12 + 40-bit hash of the LEI | ASML |

- **Securities** are listings: US listings from SEC and Massive (types CS, ADRC, ETF…, FIGIs,
  exchange MIC), Taiwanese home listings (`2330.TW`, `currency = TWD`), and US OTC lines and
  ADRs linked to foreign issuers. `ticker_history` keeps renames; `SymbolResolver` assigns
  market-wide rows to whoever held a symbol on that date, delisted securities included.
- **Cross-listings** (`crosslist.py`) link a foreign issuer to the shares it trades in the
  US. Ordinary lines match by OpenFIGI share-class FIGI (exact). ADRs match by OpenFIGI name
  to the company's ordinary line; their ratio is inferred from both prices. Companies that
  file with both the SEC and their home regulator are marked `same_as` the SEC filer and
  counted once. Name-based links are recomputed on every run.

## Point in time

- FRED values come from ALFRED vintages: each appears on its release date, as first
  printed, and changes when revised.
- Statement items carry `first_filed`: when the figure first became public. Later filings
  repeating it as a comparative don't make it newer. The latest restatement's value is
  kept, which is a small look-ahead.
- `metrics.compute(as_of)` uses figures first filed by then, prices and FX rates of that
  day, splits aligned to the day's price, and the listings trading then (delisted ones
  included). `backfill-metrics` writes monthly snapshots for screen backtests.
- COT positions appear on their Friday release; EIA weekly data on its Wednesday or
  Thursday release.

## Adding a data source

1. **Provider:** `providers/<name>.py`, a `Provider` subclass with `name`, `base_url`,
   `limits`, `auth_params()` or `headers()`. Fetch with `get` (JSON), `get_bytes` or
   `post_json`/`post_form`, always with a `dataset` and a `key` that make the response
   self-describing: the loader gets only `(key, payload, fetched_at)`.
2. **Parser:** a module that turns the payload into rows or companyfacts-shaped facts
   (`world.facts_payload`).
3. **Loader and declaration:** in the domain module, a loader taking `(session, key,
   payload, fetched_at)` registered with `registry.register(Dataset(provider, name, load,
   targets=(...), payload="bytes" | "request", snapshot=..., reference=...))`. Add the
   module to `sources.py`. Anything that writes facts targets `fundamentals`.
4. **Rebuild:** give the dataset a target; a new target needs its tables in
   `rebuild.TABLES`.
5. **Sync and CLI:** a `sync_*` function in the domain module wrapped in
   `pipeline.tracked(...)` (it records `sync_state` and commits), a command in the right
   `cli/` module, and a step in `cli/ops.py`'s daily or weekly schedule. Research series
   register with `@timeseries.source("name")`.
6. **Statements:** map new concepts in `statements.LINE_ITEMS` (or the per-region dicts).
7. **Tests:** fixtures in code (or small real files in `tests/fixtures/`), `respx` for HTTP,
   and a rebuild check where it applies. Patch the provider's limits in `tests/conftest.py`.

## Operations

- **Server:** Oracle Cloud Always Free Arm VM (Phoenix), Ubuntu 24.04, app user
  `finintel`, repo at `/home/finintel/financial_intelligence`. Reached over Tailscale; the
  API listens on `127.0.0.1:8000`. See [deploy/README.md](../deploy/README.md).
- **Containers (`compose.yaml`):** `postgres` (data on the host's block volume), `api`, and
  `cron`, all but Postgres from one image built on the server from the checkout. Data stays
  on the host (`/data`, `data/raw`).
- **Schedule (supercronic, `deploy/crontab`):** `sync-daily` Mon–Fri 23:30 UTC,
  `sync-weekly` Sun 06:00 UTC, backup 04:00 UTC. Long backfills run as detached one-off
  containers (`docker compose run -d --name ... cron fin-intel ...`).
- **Deploy:** `sudo bash deploy/install.sh` pulls `main`, builds the image, migrates, then
  restarts the containers.
- **Backups:** `deploy/backup.sh` uploads the tables that can't be rebuilt (raw index,
  portfolio) as SQL, plus the raw files present at dump time, to OCI Object Storage
  (rclone remote `oci`), and verifies both. Everything else is rebuilt from raw.
- **Storage:** PostgreSQL 16 on a 100 GB block volume (`/data`, about 30 GB of data in
  October 2026) and the raw store (gzip, about 14 GB) on the boot disk. Concurrent syncs
  write without contention; heavy research reads run alongside them.
