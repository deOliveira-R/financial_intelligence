# CLAUDE.md

Onboarding for working on this repository. Read this first, then the docs it points to.

## What this is

A personal financial-intelligence backend: it combines free official data (SEC, FRED,
EDINET, DART, TWSE, ESEF, CFTC, EIA…) and free commercial tiers (Massive, Tiingo) into
one stored, queryable dataset, and builds research tools on top. It serves four goals:
portfolio decisions (tax lots, harvesting), finding undervalued companies, following big
players (insiders, 13F, Congress), and trading indicators. Investing is meant to be
low-attention (durable picks, alerts only on exceptions); speculation is a separate,
high-attention track with its own signals.

- [docs/capabilities.md](docs/capabilities.md): what it can do, by goal, with commands and endpoints
- [docs/architecture.md](docs/architecture.md): pipeline, modules, identity, point-in-time rules, how to add a source
- [docs/data_sources.md](docs/data_sources.md): sources in use, known-unused, rejected, and paid consolidators
- [docs/roadmap.md](docs/roadmap.md) (phases A–E) and [docs/backlog.md](docs/backlog.md) (deferred work)
- [deploy/README.md](deploy/README.md): the Oracle Cloud server

## Commands

```sh
uv sync                                   # uv-managed Python 3.14 + .venv (never the system Python)
uv run pytest -q                          # ~250 tests, ~10 s; must pass before every commit
uv run ruff check . && uv run ruff format .
uv run fin-intel --help                   # every CLI command (syncs, rebuilds, research)
uv run fin-intel make-migration "message" # after changing models.py; review, then `ruff format` it
uv run fin-intel serve                    # API on http://127.0.0.1:8000/docs
```

`tests/test_migrations.py` fails if models and migrations drift apart.

## How the code is organized

```
providers/<source>.py   fetch (rate limits, retries) → raw store (every response kept)
ingest.py               LOADERS[(provider, dataset)] + sync_* functions (live syncs)
rebuild.py              TARGETS: wipe tables, replay raw through the same loaders
statements.py           standard line items from US GAAP / IFRS / J-GAAP / Korean / Taiwanese concepts
metrics.py              point-in-time company metrics (USD, splits, ADR ratios)
crosslist.py, world.py  non-SEC issuers, their US listings, SEC duplicates
timeseries.py           research specs (px:, fred:, cot:, eia:, jp:, gov:, si:, cboe:, lobby:, bills:, breadth:) → backtest.py, screener.py,
                        screentest.py, events.py
cli.py, api.py          typer CLI; read-only FastAPI
```

## Rules that keep the data trustworthy

- **Raw first, rebuildable.** Every response goes to the raw store before it's parsed.
  Loaders take only `(key, payload, fetched_at)`, so make raw keys self-describing (they
  carry whatever the loader needs). A new dataset must be registered in `LOADERS` and in
  the right `rebuild.TARGETS` entry; anything writing facts goes in `FACT_DATASETS`
  (`rebuild fundamentals` wipes all facts). See the checklist in docs/architecture.md.
- **Fetch, then load.** SQLite has one write lock and several long syncs run at once.
  Fetch everything first, then write; commit per item (per company, per day). Never hold
  a write transaction across a network call or across a whole table.
- **Point in time.** Never let a backtest see data before it was public: use
  `first_filed`, ALFRED vintages, release dates; prices and FX as of the day.
- **Reported vs derived.** Never edit provider values after load. Inferences live in
  separate tables or columns and are recomputed offline.
- **No wrong numbers.** When an input is missing or inconsistent (no FX rate, unknown ADR
  ratio, stale OTC price, ambiguous name match), leave the metric empty rather than guess.
- **Identifiers over names.** Match by CIK, FIGI, ISIN, LEI. Name matching (crosslist.py)
  uses OpenFIGI's own names, rejects ambiguity, and needs exact matches for one-word names.

## Conventions

- Branch per feature; commit only with a green `pytest`; fast-forward `main` once validated
  (tests plus a live check), then deploy. Commit messages end with the attribution lines
  the session provides.
- Tests build fixtures in code (`tests/conftest.py`), mock HTTP with `respx`, and use
  in-memory SQLite with `StaticPool`. The raw store commits on its own connection, so
  commit after direct loader calls in tests. New providers get their limits relaxed in
  `conftest.py`.
- Match the surrounding style: docstrings explain *why*, comments are sparse, line length 100.
- Keys live in `.env` (`FI_*`, see `.env.example`). They're for free services and are
  low-stakes; handle them without friction, but never commit `.env`.
- Record deferred ideas in `docs/backlog.md` rather than stopping to ask.

## Server

- `ssh fin-intel` (Oracle Arm VM, Phoenix). The app runs as user `finintel` in
  `/home/finintel/financial_intelligence`, which the SSH user can't read directly:
  `sudo -u finintel bash -c "cd /home/finintel/financial_intelligence && .venv/bin/fin-intel ..."`.
- Deploy: `ssh fin-intel 'sudo bash /home/finintel/financial_intelligence/deploy/install.sh'`
  (pull `main`, sync deps, migrate, restart the API).
- Long jobs: `sudo systemd-run --unit=NAME --uid=finintel --gid=finintel
  -p WorkingDirectory=/home/finintel/financial_intelligence <absolute path> ...`, then
  `journalctl -u NAME`. One-shot units show `activating` (not `active`) while running.
- Timers: `sync-daily` Mon–Fri 23:30 UTC, `sync-weekly` Sun 06:00 UTC, backup 04:00 UTC.
- The developer's laptop runs ProtonVPN: some sources rate-limit or fail locally (EIA
  DEMO_KEY, GLEIF, some downloads). Test those from the server.

## Gotchas

- SEC's company facts API lags some filings (2026: IFRS 2025-taxonomy 20-Fs had no figures);
  `fill-xbrl-gaps` reads the filing XBRL directly.
- Many SEC "issuers" named "…/ADR" are depositary programs with no financial statements;
  don't treat them as the company.
- Statements use facts only from `periods.FINANCIAL_FORMS` (periodic reports, registration
  statements): proxies' pay-versus-performance tables tag net income too, sometimes
  mis-scaled.
- Foreign filers often add a USD convenience translation; statements keep the reporting
  currency, and metrics convert at FRED rates (no rate → no valuation).
- Text search uses FTS5 virtual tables (`filing_text_fts`, `presidential_text_fts`) created
  in migrations 0030 and 0034 with triggers; `db.include_name` keeps autogenerate and the
  drift test from seeing them.
- `dict(session.execute(...))` breaks (Result has `.keys()`); use `.all()` first.
- Massive's free plan: 5 calls/minute and 2 years of history. DART: ~20k requests/day.
- Stooq, Naver and yfinance are deliberately not used (bot check / unofficial).
