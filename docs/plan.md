# Plan: organize, then connect (October 2026)

The working plan across sessions. Agreed with the user on 2026-10-11: clean and organize
the backend before extending it. Read this first when resuming; update the status lines as
work lands. Deferred ideas stay in [backlog.md](backlog.md); this file is the sequence.

**Status (2026-10-22):** phases 1-4 v1 deployed; phase 5 research H001-H005 done (see
Phase 5); batch 1 run 2026-10-22: H006 and H009 (regime dials) rejected, H007 (variance
premium conditioning) rejected with the premium standing, H008 (insider purchases) weak
support. Next: candidates d, e, f, k on the new price proxy and factors. Operations
(2026-10-20, below): the server runs in containers and the schedule is steps with states,
retried until they succeed. The GDELT backfill is complete (17 queries since 2017), so the
thesis trackers are unblocked. Deferred by the user: phone notifications (dropped: the
server is to be autonomous) and the Fidelity/Vanguard imports. A ProtonVPN exit node for
the tailnet is another session's work (separate compose project and tailnet node on the
same VM).

## Why

Every source was built well on its own, but the backend grew as silos (46 tables, 30 GB):
the questions that matter cross sources ("what is happening around defense?", "what did
we know about NVDA on 2022-03-31?") and needed hand-made joins. The order is: a solid base
(PostgreSQL), a clean structure (registry, domain modules), a shared vocabulary (themes and
the supply-chain graph), and only then the connecting features (timeline, dossiers,
catalog), which the signal-design session will consume.

## Phase 1: PostgreSQL (done 2026-10-11)

Why: SQLite's single write lock kept crashing concurrent jobs; the move only gets more
expensive as data grows.

- [x] Server: 100 GB block volume at `/data`, PostgreSQL 16 data there, tuned for 11 GB RAM,
  role and database `finintel`/`fin_intel` (local socket, peer auth). deploy/README.md.
- [x] Code (main, commit 0cc0203 and 2366796): Postgres-only db layer, BIGINT ids, unbounded
  strings, tsvector full-text search, migrations squashed to a 0001 baseline, rebuilds reset
  id sequences, tests on a Docker Postgres (`fi-pg`, port 5433), pg_dump backups.
- [x] Server `.env` points at Postgres; schema at 0001; weekly sync of 2026-10-11 stopped
  half-way (its committed steps are in the copy).
- [x] Copy (`deploy/sqlite_to_postgres.py`, unit `fin-intel-pgcopy`): finished 2026-10-11
  08:20 UTC, every table's row count matched (facts 94.9M, institutional_positions 56.7M,
  daily_bars 12.1M, statement_items 7.2M), sequences reset.
- [x] Verify: metrics (`/metrics/NVDA`), a timeseries (`fred:DGS10`), a full-text search and
  `/status` all answer from Postgres.
- [x] Cut over (2026-10-11 08:26 UTC): API up, `sync-weekly` run manually, timers enabled,
  backup run and verified (`essential/essential-*.dump` uploaded; `pg_restore --list` reads
  it).
- [x] Deleted `data/fin_intel.db` (SQLite, 31.5 GB) on 2026-10-11 at the user's request (Postgres
  verified, nightly backups verified).

## Phase 2: structure (done, deployed 2026-10-11)

Branch `registry` → `themes` (7 commits ahead of main): merge and deploy after phase 1 is
live, so the move and the restructure are validated separately.

- [x] `registry.py`: each raw dataset declared once by its module (`Dataset`: loader,
  payload json/bytes/request, snapshot/reference/symbol-keyed, rebuild targets);
  `sources.py` imports every declaring module; `rebuild.TABLES` lists what targets wipe.
- [x] `ingest.py` split: `pipeline.py` (tracked, synced_since, utc_today), `symbols.py`
  (SymbolResolver, get_security), `market.py`, `edgar.py`, and each domain module holds its
  loaders and syncs.
- [x] `@timeseries.source("name")` registered from domain modules.
- [x] `cli/` package by area (help panels; schedules in `cli/ops.py`), `api/` package of
  routers by area (all 40 routes and auth verified).
- [x] Merge `themes` into main, deploy, run `sync-daily` once live, check `/status`
  (2026-10-11; `graph` followed the same day: `sync-industries`, `rebuild insiders` removed
  12,399 duplicate trades, dossiers, timeline and MCP over SSH checked live).

## Phase 3: themes and the supply-chain graph

The shared vocabulary every connecting feature uses.

- [x] `themes.py` draft (13 themes: the six strategic industries with energy split into oil
  and gas vs critical minerals, plus shipping, financials, technology, agriculture, real
  estate, consumer), each mapped to SIC/universe industry, NAICS, LDA issue codes, CRS
  policy areas and subjects, a presidential-document query, ETFs, CFTC markets, FRED/EIA.
  Codes validated against the loaded data. The user found it reasonable (2026-10-11).
- [x] Layer 1 (branch `graph`): `sync-industries` stores BEA's supply-use zip raw and loads
  `io_codes`, `io_naics`, `io_uses` (detail 2007/2012/2017, summary 1997-2023, 269k cells);
  `graph.py` builds industry, summary, final-demand and theme nodes with `supplies`
  (dollars, buyer_share, supplier_share), `part_of` and `maps_to` edges; `graph`, `theme`,
  `build-graph`, `/graph/{node}`, `/themes`. Original notes: BEA input-output accounts (free,
  `https://apps.bea.gov/industry/iTables%20Static%20Files/AllTablesSUP.zip`: detailed
  2017 use table, ~400 industries keyed to NAICS; summary tables 71 industries 1997-2023).
  Edges supplier industry → buyer industry with dollars and share of the buyer's inputs,
  dated by table year. New provider/dataset/loader per the registry; raw first.
- [x] Layer 2 (same branch; themes gained curated `bea` codes for construction, defense
  gained NAICS 334511): theme graph derived: theme-to-theme edges aggregated from layer 1 through
  each theme's NAICS codes; curated edges only where codes are too coarse, marked as such.
- [x] Graph storage in Postgres: `graph_nodes` / `graph_edges` (typed: supplies, maps_to,
  part_of; source, as_of, weight), recursive-CTE queries for upstream/downstream N hops.
- [x] Layer 3a (branch `graph`): companies → BEA industries through Census's concordances
  (1987 SIC → 2017 NAICS, weights split equally per step; SEC-only and group codes fall
  back to their SIC group), companies → themes (`themes.themes_for`: universe industry or
  SIC ranges); `graph X --direction companies`. 90% of listed companies with a SIC map.
- [ ] Layer 3b, moved to phase 5 (decided 2026-10-11): company-company edges. SEC's company
  facts strip XBRL dimensions, and concentration disclosures mostly anonymize customers
  ("Customer A"); a hand-curated core would rest on memory rather than a source. The 10-K
  text already stored (filing_texts) is the better input: LLM extraction with citations.

## Phase 4: connecting features

- [x] Timeline v1 (branch `graph`, 2026-10-11): `timeline.py` reads public-dated events
  from the source tables on request rather than copying them (always current, nothing to
  rebuild; materialize later only if slow): insider, congress, 8-K/event filings, reports,
  bills, presidential documents, lobbying, contracts. Themes gained `keywords` (renamed
  from presidential_query) used for presidential text, lobbying issue descriptions and bill
  titles, since codes and policy areas alone are too coarse. 13F quarters and short-interest
  jumps added for companies; economic releases. Found and fixed on the way: insider trades stored twice when the
  dataset and the Form 4 round a price differently (`rebuild insiders` after deploy).
  Original notes: unified point-in-time event timeline: one row per public event (insider trades,
  congressional trades, 13F changes, 8-K items, bills, presidential documents, lobbying
  quarters, contract months, short interest, releases) with its public date, kind, entity
  (company, theme, member) and a link to the source row; rebuilt from the sources.
- [x] Dossiers v1 (branch `graph`): `dossier.py`, `dossier`, `/dossier/company/{ticker}`,
  `/dossier/theme/{name}`; MCP server `mcp_server.py` (`fin-intel mcp`, stdio over SSH)
  with dossiers, timeline, supply chain, filing search/text, series, catalog. Company dossiers include five
  fiscal years of annual figures; theme dossiers show speculators' positioning in
  the theme's futures (managed money or leveraged funds, 3-year index). Original notes: dossiers: company as of a date (fundamentals, metrics, ownership, insiders,
  congress, short interest, filing text, its themes' policy activity) and theme as of a date
  (spend, bills, orders, contracts, breadth, positioning, plus upstream and downstream
  themes through the graph). API endpoints, and an MCP server exposing them as tools for
  agents.
- [x] Catalog v1 (branch `graph`): `catalog.py`, `catalog [--check]`, `/catalog`; sync-daily
  ends with the check (first catch: every FRED update failing since 2026-10-09; FRED
  answers HTTP 500 to a realtime_start past its own date, which the incremental vintage
  request used; fixed on `graph`). Not yet:
  push notifications (no channel chosen; ntfy.sh is free and needs no account: ask the
  user), coverage gaps per key. Provider terms per feed (public, personal use, check terms). Original notes: data catalog and freshness: per dataset coverage, row counts, last success, gaps,
  point-in-time rule, licensing; alerts when a feed goes stale or fails (the "exceptions
  only" principle). A per-table count report also serves migration and refactor checks.

## Phase 5: the signal-design session (the user's)

Started 2026-10-11: protocol, data audit and steps in [research.md](research.md);
pre-registered hypotheses and results in [research/](research/); the user's theses in
[research/theses.md](research/theses.md).

Status 2026-10-12: survivorship-free population loaded (18k filers, dead or alive); new
sources UCDP, GDELT (`sync-news`, slow: GDELT throttles), BIS, Macrohistory, OECD panel;
study harness (`research.py`, `fin-intel study H00N --period design|validation|holdout`,
`--freeze`). Results: H001 distress alert 7.3x lift in the holdout (partial support,
holdout spent; live as distress alerts; edge over a correct Altman Z'' 7.3 vs 4.9);
H002 copper doesn't lead, H004 its acceleration rejected on unseen months; H003 the boom
and credit gap warn of banking crises (not testable after 2015: no crises; failed for
equities), the reversal comes too late, a public offset defuses it.

Next, agreed with the user (2026-10-12), in order:
- [x] **H001 as a live exception alert** (2026-10-12, distress.py): the frozen logit
  scores every eligible filer daily in sync-daily (after refreshing watched companies'
  events); watched companies in the population's top 2% are flagged, entries marked NEW,
  with their signals. `fin-intel distress [--all|--score]`, API `/distress`, MCP
  `distress_alerts`, table distress_scores. Building it found Z'' mostly missing in H001
  (10-Q balance sheets): the benchmark was corrected (H001 errata; edge 7.3 against 4.9,
  not twice); fixed signals go into a new hypothesis.
- [x] **H003 narrower claims on the time holdout** (2026-10-12): no crises after 2008 in
  the data, so the crisis claims aren't testable on time (they rest on 1870-2014 and the
  out-of-country check, where the boom plus a wide credit gap triples three-year crisis
  rates); the equity part was rejected (0.79 after 2015, many "booms" being 2020's GDP
  collapse widening the gap).
- [x] **H004: copper acceleration at 3 months** (2026-10-12): pre-registered, one
  variant, run on months H002 never saw: rejected (validation out-of-sample R² -0.03,
  -0.004 without the pandemic; holdout -0.18; no sector signal). China's steel output
  isn't on FRED: backlog.
- [x] **H005: what copper measures** (2026-10-12, the user's follow-up): World Bank Pink
  Sheet loaded (metals since 1960); seven pre-registered tests (China's credit, whose
  activity, nowcasts, copper/gold against the 10-year yield): none of the three claims
  supported; copper is a coincident, timely price of the industrial cycle.
Later: trackers for theses 2, 4, 6-8 once GDELT's load completes; new distress signals
(going concern, short-interest jumps) as a new hypothesis.

**Batch 1 (2026-10-22, from [research/candidates.md](research/candidates.md)):** the
user's direction is a hypothesis-driven search for ways of making money (risk, buy or
not, options). Candidates ranked by source of return and data readiness; the first three
pre-registered before any result:
- [x] **H006** trend + credit regime filter (research/H006-regime-filter.md): **rejected**
  (2026-10-22). The trend rule chosen on 1993-2015 (drawdown 0.30 of buy-and-hold's,
  +2.4 points a year) lost 4.7 points a year in 2016-2023 with no drawdown edge over a
  constant 79% allocation; the fast, V-shaped drawdowns since 2016 are its failure mode.
  Monthly decisions beat daily everywhere. Follow-up: volatility-managed exposure
  (candidates row q), not a retune.
- [x] **H007** variance risk premium conditional on VIX minus realized volatility
  (research/H007-variance-premium.md): **conditioning rejected, the premium stands**
  (2026-10-22). Cboe's index histories loaded (`volatility.py`, `sync-volatility`, daily
  step); PUT is daily only from 2007, so the first design run was void and re-registered.
  Selling index options paid 0.4-0.6% a month over T-bills in every period (BXMD 1993-2023
  t = 3.2), with -33% in 2008, and trailed the index since 2016.
- [x] **H008** opportunistic insider purchases (research/H008-opportunistic-insiders.md):
  **weak support as registered; the holdout supports** (2026-10-22). +0.8% a quarter over
  size peers in design (t 2.6), +0.3% in validation (t 0.5), +1.1% in the holdout (t 2.7),
  +1.8 to +2.5% over four quarters: a modest, consistently signed tilt, significant in two
  of three periods; re-read after eight new quarters. The routine/opportunistic distinction
  adds little: the purchase itself carries the information. Built for it: the 13F price
  proxy over every filer (`quarterly_prices`, `quarterly_price_parts`, from 2013-Q2),
  OpenFIGI fixes (US listing, share-class FIGI, `ID_CINS`, `sync-13f --relink`) and the
  issuer bridges (`cik_source`); coverage 43% (2013) to 81% (2022) of flagged companies.
- [x] **H009** volatility-managed exposure (research/H009-volatility-managed.md,
  candidates row q): **rejected** (2026-10-22). Drawdowns a third to a half smaller than
  buy-and-hold's in every period and on both assets, but 3-4 points a year of return and
  no risk-adjusted gain (validation SPY Sharpe 0.73 vs 0.67 with a wide interval, RSP 0.48
  vs 0.57; holdout below on both). With H006: no monthly timing rule tested improved
  risk-adjusted returns since 2016; drawdown control was only bought with return.
- [x] Ken French factor returns loaded (`factors.py`, `sync-factors`, weekly; `ff:` series).
- [x] **H010** best ideas of concentrated, patient 13F managers (research/H010-best-ideas.md,
  candidates row d): **weak support** (2026-10-22). +0.8 / +1.3 / +3.9% over four quarters
  (t 0.7 / 0.3 / 1.6) against -0.4 / -0.1 / +1.0% for any filer's best ideas: the screen
  selects better buyers, by 1-3 points a year, never significantly, on 60-90 companies a
  quarter. Consensus (two or more managers, ~10 names) +15.7% (t 3.1) in the holdout only:
  queued as its own hypothesis. Built: `managers.py` (`manager_quarters`,
  `manager_best_ideas`, `derive-managers` weekly after 13F).
- Next: combine H008 and H010 flags (insider purchase and a patient manager's best idea in
  the same quarter), candidates e (PEAD), f (spin-offs), k (sector momentum), i (VIX roll).
- [ ] Data in parallel: watchlist option chains recorded daily (check Massive's options
  tier), VIX futures (CFE) for candidate i.
- Also found: FRED's ICE BofA spreads start 2023-10 (license); Moody's `BAA10Y` added as
  the long-history credit spread. OpenFIGI answers a CUSIP with at most ten listings and
  needs `ID_CINS` for letter-first identifiers; SEC's 13F data sets begin 2013-Q2.

Hypotheses, analysis skills, evals and hill-climbing on the historical data, using the
timeline, dossiers and graph. LLM skills extract supplier/customer links and policy
relevance from filing and presidential text (feeding graph layer 3).

## Operations: autonomous server (2026-10-20)

The user's goal: a server that runs and repairs itself, with no phone notifications. Later
an Anthropic API key on the server lets a model diagnose `failed` steps from their history
and fix what it can; a front end shows `/status` with a notification icon.

- [x] **Containers** (compose: postgres, api, cron; data on the host). Settings in `.env`
  with defaults in `compose.yaml` (TrueNAS-style: images pinned by digest, the image holds
  only code); read-only app containers, all capabilities dropped, healthchecks, memory
  limits. Moved from the host install on 2026-10-20 reusing the database directory.
- [x] **Steps with states** (`jobs.py`, `cli/schedule.py`): the schedule marks steps due; a
  reconciler every 10 min runs them in child processes with time limits and retries by
  error kind until they succeed; `jobs status`, `/status`, `/jobs/<step>/events`, MCP
  `job_status`. GDELT runs one query an hour; 13F freshness is quarterly.
- [ ] Model-driven diagnosis of failed steps (needs the API key; backlog).
- [x] **Dashboard prototype** (2026-10-20, `frontend/`, served at `/` over the tailnet):
  Markets, Company, Portfolio tabs; system status as a header dot that stays quiet while
  all is well (page with steps, attempts and output, feeds); Settings to paste keys and edit
  the watchlist (saved in `app_settings` over `.env`, retrying blocked steps), with each
  key's status. Next: use it, then revisit what each tab shows and how.

## Working rules for this plan

- One phase at a time; tests green before every commit; validate live before merging the
  next phase. Heavy writers run alone (Postgres removes the lock crashes, not the I/O).
- Raw first and rebuildable for every new dataset (BEA included); inferences (graph edges
  derived from data, curated edges) live in their own tables and are recomputed.
- Update the checkboxes and status lines here as work lands.
