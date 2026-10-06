import logging
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import typer
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from fin_intel import derive, ingest
from fin_intel.config import get_settings
from fin_intel.db import init_db, session_factory
from fin_intel.ingest import SNAPSHOT_DATASETS
from fin_intel.models import CongressReport, DailyBar, SyncRun, SyncState
from fin_intel.providers import (
    FredProvider,
    MassiveProvider,
    ProviderError,
    QuotaExceededError,
    SecProvider,
    TiingoProvider,
)
from fin_intel.raw import default_store, prune
from fin_intel.rebuild import TARGETS, rebuild

app = typer.Typer(help="Financial Intelligence data backend", no_args_is_help=True)


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    # Under systemd stdout is a pipe, block-buffered by default: progress would show up late.
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    init_db()


LOCK_RETRIES, LOCK_PAUSE = 3, 30.0


def _retry_locked(session: Session, fn: Callable[[Session, str], Any], item: str) -> Any:
    """fn(session, item), retried when SQLite's write lock stays taken past its timeout (a
    long write in another job). Items are idempotent, so a retry redoes the item cleanly."""
    for attempt in range(LOCK_RETRIES + 1):
        try:
            return fn(session, item)
        except OperationalError as exc:
            if "database is locked" not in str(exc) or attempt == LOCK_RETRIES:
                raise
            session.rollback()
            typer.secho(f"  {item}: database locked; retrying in {LOCK_PAUSE:.0f}s", err=True)
            time.sleep(LOCK_PAUSE)


def _run(job: str, items: list[str], fn: Callable[[Session, str], Any]) -> None:
    """Run fn per item, recording the run in sync_runs. A quota error stops the batch.

    The run record lives in its own session with short transactions: a pending write in the
    work session would hold SQLite's write lock while providers record raw responses.
    """
    factory = session_factory()
    with factory() as session, factory() as bookkeeping:
        run = SyncRun(job=job, started_at=datetime.now(UTC), status="running")
        bookkeeping.add(run)
        bookkeeping.commit()
        failed: list[str] = []
        message = None
        try:
            for i, item in enumerate(items):
                try:
                    typer.echo(f"{job} {item}: {_retry_locked(session, fn, item)} rows")
                    run.items_ok += 1
                except QuotaExceededError as exc:
                    failed.append(item)
                    skipped = items[i + 1 :]
                    message = f"{exc}; skipped: {' '.join(skipped)}" if skipped else str(exc)
                    typer.secho(f"{job} {item}: {message}", fg="red", err=True)
                    break
                except ProviderError as exc:
                    failed.append(item)
                    typer.secho(f"{job} {item}: {exc}", fg="red", err=True)
                bookkeeping.commit()
        except BaseException as exc:
            # Release the work session's write lock first, or recording the crash below
            # waits on it (SQLite) and reports a lock timeout instead of the real error.
            session.rollback()
            message = f"crashed: {exc!r}"[:1000]
            raise
        finally:
            run.items_failed = len(items) - run.items_ok
            run.status = "ok" if not run.items_failed else "partial" if run.items_ok else "failed"
            run.finished_at = datetime.now(UTC)
            run.message = message or (f"failed: {' '.join(failed)}" if failed else None)
            bookkeeping.commit()
    if run.items_failed:
        raise typer.Exit(1)


@app.command()
def sync_tickers() -> None:
    """Load all SEC-registered tickers and their CIKs."""
    sec = SecProvider(raw_store=default_store())
    _run("sync-tickers", ["all"], lambda s, _: ingest.sync_tickers(s, sec))


@app.command()
def sync_fundamentals(tickers: list[str]) -> None:
    """Load SEC XBRL financial facts for the given tickers."""
    sec = SecProvider(raw_store=default_store())
    _run("sync-fundamentals", tickers, lambda s, t: ingest.sync_fundamentals(s, sec, t))


@app.command()
def sync_fundamentals_bulk(
    zip_path: Annotated[
        str | None, typer.Option("--zip", help="Use an already downloaded companyfacts.zip")
    ] = None,
    all_filers: Annotated[
        bool, typer.Option(help="Every SEC filer, not just issuers with a security")
    ] = False,
) -> None:
    """Load SEC fundamentals for every tracked issuer from the nightly bulk file.

    The first run loads everything (hours); later runs reload only companies whose data
    changed. Each company becomes its own raw response, as with sync-fundamentals.
    """
    from pathlib import Path

    from fin_intel.config import get_settings as settings

    store = default_store()
    downloaded = zip_path is None
    path = (
        Path(zip_path) if zip_path else Path(settings().raw_dir).parent / "tmp" / "companyfacts.zip"
    )

    def work(session: Session, _: str) -> int:
        if downloaded:
            typer.echo("downloading companyfacts.zip ...")
            SecProvider(raw_store=store).download_bulk_company_facts(path)
        ciks = None if all_filers else ingest.tracked_ciks(session)

        def progress(n: int, total: int) -> None:
            if n % 250 == 0 or n == total:
                typer.echo(f"  {n}/{total} companies")

        loaded, unchanged = ingest.load_bulk_company_facts(session, store, path, ciks, progress)
        typer.echo(f"loaded {loaded}, unchanged {unchanged}")
        return loaded

    try:
        _run("sync-fundamentals-bulk", ["bulk"], work)
    finally:
        if downloaded:
            path.unlink(missing_ok=True)


@app.command()
def sync_insiders(
    since: Annotated[
        str | None,
        typer.Option(help="First quarterly data set, e.g. 2024q1; default: 2 years back"),
    ] = None,
    until: Annotated[
        str | None, typer.Option(help="Last day of daily filings; default: yesterday")
    ] = None,
) -> None:
    """Load insider transactions: SEC's quarterly data sets, then each day's Form 4s since
    the latest data set (one request per filing, ~1,500 per day at SEC's rate limit)."""
    store = default_store()
    sec = SecProvider(raw_store=store)
    today = date.today()
    first = since or f"{today.year - 2}q{(today.month - 1) // 3 + 1}"
    datasets = {q: url for q, url in sec.list_insider_datasets().items() if q >= first}
    loaded = set(store.latest_hashes("sec", "insider_dataset"))
    pending = sorted(q for q in datasets if q not in loaded)
    if pending:
        _run(
            "sync-insiders",
            pending,
            lambda s, q: ingest.sync_insider_dataset(s, sec, q, datasets[q]),
        )

    latest = max(datasets, default=None)
    if latest is None:
        return
    year, quarter = int(latest[:4]), int(latest[5])
    start = date(year + quarter // 4, quarter % 4 * 3 + 1, 1)  # day after the quarter
    end = date.fromisoformat(until) if until else today - timedelta(days=1)
    with session_factory()() as session:
        done = set(
            session.scalars(
                select(SyncState.key).where(
                    SyncState.provider == "sec",
                    SyncState.dataset == "insider_day",
                    SyncState.last_success.is_not(None),
                )
            )
        )
    days = [d.isoformat() for d in _weekdays(start, end) if d.isoformat() not in done]
    if days:
        _run(
            "sync-insiders",
            days,
            lambda s, d: ingest.sync_insider_day(s, sec, date.fromisoformat(d)),
        )


@app.command("sync-13f")
def sync_13f(
    files: Annotated[int, typer.Option(help="Most recent 13F data set files to load")] = 4,
    since: Annotated[
        str | None, typer.Option(help="Load every data set from this date, e.g. 2013-01-01")
    ] = None,
) -> None:
    """Load institutional holdings (13F data sets, three months each), then map their
    CUSIPs to securities via OpenFIGI (set FI_OPENFIGI_API_KEY to make that ~100x faster)."""
    from fin_intel.providers import OpenFigiProvider

    store = default_store()
    sec = SecProvider(raw_store=store)
    available = sec.list_13f_datasets()
    wanted = (
        sorted(p for p in available if p.split("_")[-1] >= since)
        if since
        else sorted(available)[-files:]
    )
    with session_factory()() as session:
        loaded = set(
            session.scalars(
                select(SyncState.key).where(
                    SyncState.provider == "sec",
                    SyncState.dataset == "13f_dataset",
                    SyncState.last_success.is_not(None),
                )
            )
        )
    pending = [p for p in wanted if p not in loaded]
    if pending:
        _run("sync-13f", pending, lambda s, p: ingest.sync_13f_dataset(s, sec, p, available[p]))
    openfigi = OpenFigiProvider(raw_store=store)

    def progress(done: int, total: int) -> None:
        if done % 1000 < openfigi.batch or done == total:
            typer.echo(f"  mapped {done}/{total} CUSIPs")

    _run("sync-13f", ["cusips"], lambda s, _: ingest.sync_cusip_mappings(s, openfigi, progress))


@app.command()
def sync_congress(
    since: Annotated[
        int | None, typer.Option(help="First year of reports to load; default: 2 years back")
    ] = None,
    chamber: Annotated[str, typer.Option(help="house, senate or all")] = "all",
) -> None:
    """Load members of Congress's trades (Periodic Transaction Reports): each chamber's
    filing index, then every electronic report not parsed yet."""
    from fin_intel import congress
    from fin_intel.providers import HouseProvider, SenateProvider

    store = default_store()
    today = date.today()
    first = since or today.year - 2
    if chamber in ("house", "all"):
        house = HouseProvider(raw_store=store)
        loaded = set(store.latest_hashes("house", "fd_index"))
        # Past years' indexes are final; the current one grows daily.
        years = [str(y) for y in range(first, today.year + 1) if str(y) not in loaded]
        years = sorted(set(years) | {str(today.year)})
        _run("sync-congress", years, lambda s, y: ingest.sync_house_index(s, house, int(y)))
        with session_factory()() as session:
            pending = {
                r.doc_id: r.year or (r.filed.year if r.filed else today.year)
                for r in congress.pending_reports(session, "house")
                if (r.year or first) >= first
            }
        if pending:
            _run(
                "sync-congress",
                list(pending),
                lambda s, d: ingest.sync_house_ptr(s, house, d, pending[d]),
            )
    if chamber in ("senate", "all"):
        senate = SenateProvider(raw_store=store)
        with session_factory()() as session:
            latest = session.scalar(
                select(func.max(CongressReport.filed)).where(CongressReport.chamber == "senate")
            )
        # An explicit --since backfills from that year; otherwise pick up where we are.
        start = (
            date(since, 1, 1)
            if since
            else latest - timedelta(days=14)
            if latest
            else date(first, 1, 1)
        )
        _run(
            "sync-congress",
            [start.isoformat()],
            lambda s, d: ingest.sync_senate_index(s, senate, date.fromisoformat(d)),
        )
        with session_factory()() as session:
            ids = [r.doc_id for r in congress.pending_reports(session, "senate")]
        if ids:
            _run("sync-congress", ids, lambda s, d: ingest.sync_senate_ptr(s, senate, d))


@app.command("sync-dart")
def sync_dart(
    corp: Annotated[list[str] | None, typer.Option(help="Only these DART corp codes")] = None,
    limit: Annotated[
        int, typer.Option(help="Most reports this run (DART: ~20k requests/day)")
    ] = 8000,
    first_year: Annotated[int, typer.Option(help="Oldest business year to load")] = 2015,
) -> None:
    """Load Korean listed companies' financial statements from DART, newest reports first;
    each run continues where the last one stopped (within the daily request quota)."""
    from fin_intel.models import Issuer, SyncState
    from fin_intel.providers import DartProvider

    store = default_store()
    provider = DartProvider(raw_store=store)
    with session_factory()() as session:
        fresh = session.scalar(
            select(SyncState.last_success).where(
                SyncState.provider == "dart", SyncState.dataset == "corp_codes"
            )
        )
    if fresh is not None and fresh.tzinfo is None:  # SQLite returns naive datetimes
        fresh = fresh.replace(tzinfo=UTC)
    if fresh is None or fresh < datetime.now(UTC) - timedelta(days=7):
        _run("sync-dart", ["corp codes"], lambda s, _: ingest.sync_dart_corps(s, provider))
    with session_factory()() as session:
        listed = session.execute(
            select(Issuer.source_id, Issuer.fiscal_month).where(
                Issuer.source == "dart", Issuer.home_ticker.is_not(None)
            )
        ).all()
    corps = sorted(c for c, _ in listed if not corp or c in corp)
    profiles = [c for c, month in listed if month is None and c in corps]
    if profiles:
        _run("sync-dart", profiles, lambda s, c: ingest.sync_dart_company(s, provider, c))
    done = {k.rsplit("|", 1)[0] for k in store.latest_hashes("dart", "statements") if k}
    periods = ingest.dart_periods(date.today(), first_year)
    latest = periods[0] if periods else None
    items = [
        f"{c}|{year}|{report}"
        for year, report in periods
        for c in corps
        if f"{c}|{year}|{report}" not in done
    ][:limit]
    if items:
        _run(
            "sync-dart",
            items,
            lambda s, item: ingest.sync_dart_report(
                s,
                provider,
                item.split("|")[0],
                int(item.split("|")[1]),
                item.split("|")[2],
                shares=(int(item.split("|")[1]), item.split("|")[2]) == latest
                or item.endswith("11011"),
            ),
        )


@app.command("sync-edinet")
def sync_edinet(
    since: Annotated[str, typer.Option(help="First filing day, YYYY-MM-DD")] = "2024-04-01",
    until: Annotated[str | None, typer.Option(help="Last filing day; default: yesterday")] = None,
) -> None:
    """Load Japanese listed companies' annual, quarterly and half-year reports from EDINET,
    one filing day at a time, newest first; days already loaded are skipped (the last week
    is rechecked for late additions)."""
    from fin_intel.models import SyncState
    from fin_intel.providers import EdinetProvider

    provider = EdinetProvider(raw_store=default_store())
    end = date.fromisoformat(until) if until else date.today() - timedelta(days=1)
    start = date.fromisoformat(since)
    with session_factory()() as session:
        done = set(
            session.scalars(
                select(SyncState.key).where(
                    SyncState.provider == "edinet",
                    SyncState.dataset == "documents",
                    SyncState.last_success.is_not(None),
                )
            )
        )
    recent = end - timedelta(days=7)
    days = []
    for i in range((end - start).days + 1):
        day = end - timedelta(days=i)
        if day.isoformat() not in done or day >= recent:
            days.append(day.isoformat())
    if days:
        _run(
            "sync-edinet",
            days,
            lambda s, d: ingest.sync_edinet_day(s, provider, date.fromisoformat(d)),
        )


@app.command("sync-esef")
def sync_esef(
    since: Annotated[str, typer.Option(help="Oldest fiscal year end to load")] = "2023-01-01",
) -> None:
    """Load European listed companies' annual reports (ESEF, filings.xbrl.org): the latest
    version of each company's report for every fiscal year since `since`."""
    from fin_intel import esef
    from fin_intel.providers import EsefProvider

    store = default_store()
    provider = EsefProvider(raw_store=store)
    found: dict[tuple[str, str], dict] = {}
    page = 1
    while True:
        index = provider.fetch_index(page)
        rows = esef.filings(index)
        for f in rows:
            if (f["period_end"] or "") < since:
                continue
            key = (f["lei"], f["period_end"])
            if key not in found or (f["version"], f["added"]) > (
                found[key]["version"],
                found[key]["added"],
            ):
                found[key] = f
        if not (index.get("links") or {}).get("next") or not index.get("data"):
            break
        page += 1
    loaded = {"|".join(k.split("|")[:2]) for k in store.latest_hashes("esef", "report") if k}
    pending = {f"{lei}|{end}": f for (lei, end), f in found.items() if f"{lei}|{end}" not in loaded}
    typer.echo(f"{len(found)} reports since {since}, {len(pending)} to load")
    if pending:
        _run(
            "sync-esef",
            list(pending),
            lambda s, k: ingest.sync_esef_report(s, provider, pending[k]),
        )


@app.command("sync-tw-prices")
def sync_tw_prices(
    since: Annotated[str, typer.Option(help="First trading day, YYYY-MM-DD")] = "2024-10-01",
) -> None:
    """Load Taiwanese listed (TWSE) and OTC (TPEx) companies' daily prices, one request per
    exchange and trading day; days already loaded are skipped (the last few rechecked)."""
    from fin_intel.models import SyncState
    from fin_intel.providers import TwseProvider

    provider = TwseProvider(raw_store=default_store())
    end = date.today()
    with session_factory()() as session:
        done = set(
            session.scalars(
                select(SyncState.key).where(
                    SyncState.provider == "twse",
                    SyncState.dataset == "prices",
                    SyncState.last_success.is_not(None),
                )
            )
        )
    items = [
        f"{market}|{d.isoformat()}"
        for d in reversed(_weekdays(date.fromisoformat(since), end))
        for market in ("twse", "tpex")
        if f"{market}|{d.isoformat()}" not in done or (end - d).days <= 3
    ]
    if items:
        _run(
            "sync-tw-prices",
            items,
            lambda s, item: ingest.sync_tw_prices(
                s, provider, item.split("|")[0], date.fromisoformat(item.split("|")[1])
            ),
        )


@app.command("sync-crosslist")
def sync_crosslist() -> None:
    """Link companies from other regulators to their US listings (OTC ordinary lines by
    share class, ADRs by name), so they can be valued with US prices (crosslist.py)."""
    from fin_intel.providers import GleifProvider, OpenFigiProvider

    store = default_store()
    openfigi, gleif = OpenFigiProvider(raw_store=store), GleifProvider(raw_store=store)

    def work(session: Session, _: str) -> int:
        stats = ingest.sync_crosslist(session, openfigi, gleif)
        typer.echo(", ".join(f"{k} {v}" for k, v in stats.items()))
        return sum(stats.values())

    _run("sync-crosslist", ["all"], work)


@app.command("sync-twse")
def sync_twse() -> None:
    """Load Taiwan's listed (TWSE) and OTC (TPEx) companies: profiles, then the latest
    quarter's income statements and balance sheets in every industry format."""
    from fin_intel.providers import TwseProvider
    from fin_intel.providers.twse import INDUSTRIES, MARKETS

    provider = TwseProvider(raw_store=default_store())
    tables = ["t187ap03"] + [f"{s}_{i}" for s in ("t187ap06", "t187ap07") for i in INDUSTRIES]
    items = [f"{m}|{t}" for m in MARKETS for t in tables]
    today = date.today()
    _run(
        "sync-twse",
        items,
        lambda s, item: ingest.sync_twse_table(s, provider, *item.split("|"), today),
    )


@app.command("fill-xbrl-gaps")
def fill_xbrl_gaps(
    cik: Annotated[list[int] | None, typer.Option(help="Only these issuers")] = None,
) -> None:
    """Read recent periodic reports' XBRL straight from the filings for issuers whose
    financials are stale (SEC's company facts can lag, e.g. new IFRS taxonomies)."""
    sec = SecProvider(raw_store=default_store())
    with session_factory()() as session:
        ciks = cik or ingest.stale_issuers(session)
    typer.echo(f"{len(ciks)} issuers with stale financials")
    if ciks:
        _run(
            "fill-xbrl-gaps",
            [str(c) for c in ciks],
            lambda s, c: ingest.sync_xbrl_gaps(s, sec, int(c)),
        )


@app.command("sync-events")
def sync_events(
    cik: Annotated[list[int] | None, typer.Option(help="Only these SEC filers")] = None,
    refresh_days: Annotated[
        int, typer.Option(help="Skip filers synced successfully within this many days")
    ] = 6,
) -> None:
    """Load corporate events from SEC filings (8-K items such as earnings releases,
    acquisitions, restatements; 13D/13G stakes; late filings; delistings) for every SEC
    filer with a primary listing, including the full history on first run. Filers synced
    recently are skipped, so a run stopped by SEC throttling resumes where it left off."""
    from fin_intel import metrics

    sec = SecProvider(raw_store=default_store())
    with session_factory()() as session:
        ciks = cik or sorted(c for c in metrics.primary_securities(session) if c < 10**10)
        if not cik:
            fresh = ingest.synced_since(
                session, "sec", "events", datetime.now(UTC) - timedelta(days=refresh_days)
            )
            ciks = [c for c in ciks if str(c) not in fresh]
    if ciks:
        _run("sync-events", [str(c) for c in ciks], lambda s, c: ingest.sync_events(s, sec, int(c)))


@app.command()
def sync_sic() -> None:
    """Look up SIC codes (industry) of issuers with a primary listing that lack one (SEC
    submissions, one request each; later runs only fetch new issuers)."""
    sec = SecProvider(raw_store=default_store())
    with session_factory()() as session:
        ciks = [str(c) for c in ingest.issuers_without_sic(session)]
    if ciks:
        _run("sync-sic", ciks, lambda s, c: ingest.sync_submissions(s, sec, int(c)))


@app.command()
def sync_adr_shares(
    limit: Annotated[int, typer.Option(help="Most ADRs to refresh this run")] = 150,
) -> None:
    """Refresh ADRs' depositary share counts from Massive (for their market caps), oldest
    first; each is refreshed every four weeks."""
    massive = MassiveProvider(raw_store=default_store())
    with session_factory()() as session:
        tickers = ingest.due_listing_shares(session, limit=limit)
    if tickers:
        _run("sync-adr-shares", tickers, lambda s, t: ingest.sync_listing_shares(s, massive, t))


@app.command()
def sync_calendar() -> None:
    """Load the economic release calendar for the tracked FRED series (plus FOMC and Bank
    of Japan meetings)."""
    from fin_intel.providers import BojProvider, FedProvider

    store = default_store()
    fred, fed = FredProvider(raw_store=store), FedProvider(raw_store=store)
    boj = BojProvider(raw_store=store)
    _run("sync-calendar", ["all"], lambda s, _: ingest.sync_release_calendar(s, fred, fed, boj))


@app.command("sync-japan")
def sync_japan(
    history: Annotated[bool, typer.Option(help="Also reload the JGB history file")] = False,
) -> None:
    """Load the JGB yield curve (this month; the full history on first run or with
    --history) and Japan's weekly portfolio flows from the Ministry of Finance."""
    from fin_intel.providers import MofProvider

    mof = MofProvider(raw_store=default_store())
    _run("sync-japan", ["mof"], lambda s, _: ingest.sync_japan(s, mof, history))


@app.command("sync-filing-text")
def sync_filing_text(
    cik: Annotated[list[int] | None, typer.Option(help="Only these SEC filers")] = None,
    since: Annotated[str, typer.Option(help="Filed on or after (YYYY-MM-DD)")] = "2023-01-01",
) -> None:
    """Fetch 10-K/10-Q narrative sections (risk factors, MD&A) and earnings press releases
    as searchable text, for the deep-history universe's companies (or --cik)."""
    from fin_intel import universe

    sec = SecProvider(raw_store=default_store())
    ciks = cik or sorted({m.cik for m in universe.read() if m.cik})
    start = date.fromisoformat(since)
    _run(
        "sync-filing-text",
        [str(c) for c in ciks],
        lambda s, c: ingest.sync_filing_text(s, sec, int(c), start),
    )


@app.command("search-filings")
def search_filings(
    query: str,
    ticker: Annotated[list[str] | None, typer.Option(help="Only these companies")] = None,
    since: Annotated[str | None, typer.Option(help="Filed on or after (YYYY-MM-DD)")] = None,
    limit: int = 20,
) -> None:
    """Full-text search over stored filing text, e.g. '"export controls" NEAR china'."""
    from fin_intel import filing_text

    with session_factory()() as session:
        ciks = None
        if ticker:
            securities = [ingest.get_security(session, t) for t in ticker]
            ciks = [s.cik for s in securities if s is not None and s.cik]
        hits = filing_text.search(
            session, query, ciks, date.fromisoformat(since) if since else None, limit
        )
    for h in hits:
        typer.echo(
            f"{h.filed} {h.form:5} {h.section:12} cik {h.cik} {h.accession}\n    {h.snippet}"
        )


@app.command("sync-funds")
def sync_funds(
    fund: Annotated[list[str] | None, typer.Option(help="Only these fund tickers")] = None,
) -> None:
    """Load index ETF holdings (N-PORT reports since 2019) for the tracked funds in
    funds.py: point-in-time index membership and weights."""
    sec = SecProvider(raw_store=default_store())
    series = ingest.fund_series(sec)
    tickers = [t.upper() for t in fund] if fund else sorted(series)
    _run("sync-funds", tickers, lambda s, t: ingest.sync_fund(s, sec, t, series[t]))


@app.command("fund-holdings")
def fund_holdings(
    fund: str,
    as_of: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: latest")] = None,
    limit: Annotated[int, typer.Option(help="Largest holdings to show")] = 25,
) -> None:
    """A tracked fund's holdings in its latest report public on a date (index membership)."""
    from fin_intel import funds

    with session_factory()() as session:
        rows = funds.members(session, fund, date.fromisoformat(as_of) if as_of else None)
    if not rows:
        typer.echo(f"no holdings for {fund}; run sync-funds")
        raise typer.Exit(1)
    first = rows[0]
    typer.echo(f"{fund.upper()}: {len(rows)} holdings, {first['period']} (filed {first['filed']})")
    for r in rows[:limit]:
        typer.echo(f"  {r['weight'] or 0:6.2f}%  {r['cusip'] or r['isin'] or '':12}  {r['name']}")


@app.command("sync-cboe")
def sync_cboe() -> None:
    """Load Cboe options volume and put/call ratios: the archive since 2006 on first run,
    then each trading day's statistics."""
    from fin_intel.providers import CboeProvider

    cboe = CboeProvider(raw_store=default_store())
    _run("sync-cboe", ["cboe"], lambda s, _: ingest.sync_cboe(s, cboe, date.today()))


@app.command("sync-short-interest")
def sync_short_interest() -> None:
    """Load FINRA short interest (every stock, twice a month since December 2017): new
    settlement dates, and recent ones again until revisions settle."""
    from fin_intel.providers import FinraProvider

    finra = FinraProvider(raw_store=default_store())
    with session_factory()() as session:
        dates = ingest.short_interest_due(session, finra, date.today())
    if dates:
        _run(
            "sync-short-interest",
            [d.isoformat() for d in dates],
            lambda s, d: ingest.sync_short_interest(s, finra, date.fromisoformat(d)),
        )


@app.command("sync-contracts")
def sync_contracts(
    limit: Annotated[int | None, typer.Option(help="Most months this run")] = None,
) -> None:
    """Load federal contract obligations by industry (NAICS) from USAspending, monthly
    since October 2007; recent months are refetched until they settle."""
    from fin_intel.providers import UsaspendingProvider

    provider = UsaspendingProvider(raw_store=default_store())
    with session_factory()() as session:
        months = ingest.contract_months_due(session, date.today())[:limit]
    if months:
        _run(
            "sync-contracts",
            [m.isoformat() for m in months],
            lambda s, m: ingest.sync_contracts_month(s, provider, date.fromisoformat(m)),
        )


@app.command()
def carry(
    as_of: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: latest")] = None,
) -> None:
    """Yen carry trade gauge: differentials, carry-to-risk, crowding, flows, and flags
    worth attention (carry.py)."""
    from fin_intel import carry as gauge_module

    with session_factory()() as session:
        g = gauge_module.gauge(session, date.fromisoformat(as_of) if as_of else None)

    def fmt(v: float | None, spec: str) -> str:
        return "-" if v is None else format(v, spec)

    typer.echo(f"yen carry gauge as of {g.as_of}   next BOJ {g.next_boj}   next FOMC {g.next_fomc}")
    typer.echo(f"{'':42}{'value':>12}{'1m change':>12}{'3y pctile':>11}")
    for r in g.readings:
        relative = r.name in ("usd_jpy", "mxn_jpy", "aud_jpy")
        change = fmt(r.change_1m, "+.1%" if relative else "+.2f")
        typer.echo(
            f"{r.name:42}{fmt(r.value, ',.2f'):>12}{change:>12}{fmt(r.percentile_3y, '.0%'):>11}"
        )
    for flag in g.flags or ["no flags"]:
        typer.echo(f"  ! {flag}" if g.flags else f"  {flag}")


@app.command()
def calendar(
    days: Annotated[int, typer.Option(help="Days ahead")] = 14,
    daily: Annotated[bool, typer.Option(help="Include daily releases (rates, VIX...)")] = False,
) -> None:
    """Upcoming economic releases and the tracked series each updates."""
    from fin_intel import releases

    with session_factory()() as session:
        for e in releases.upcoming(session, days, daily=daily):
            typer.echo(f"{e.date}  {e.release or e.release_id:<45} {' '.join(e.series)}")


@app.command()
def sync_eia(
    series: Annotated[list[str] | None, typer.Argument(help="EIA IDs or aliases")] = None,
) -> None:
    """Load EIA weekly energy data (inventories, production, refining, demand, gas storage);
    default: every series in energy.py whose next weekly release is out."""
    from fin_intel import energy
    from fin_intel.models import EconomicObservation
    from fin_intel.providers import EiaProvider

    eia = EiaProvider(raw_store=default_store())
    if series:
        ids = [energy.resolve(s) for s in series]
    else:  # only series whose next weekly release is out (the shared key is rate-limited)
        with session_factory()() as session:
            latest = dict(
                session.execute(
                    select(EconomicObservation.series_id, func.max(EconomicObservation.date))
                    .where(EconomicObservation.series_id.in_(list(energy.SERIES)))
                    .group_by(EconomicObservation.series_id)
                ).all()
            )
        ids = [i for i in energy.SERIES if energy.due(i, latest.get(i), date.today())]
        if not ids:
            typer.echo("EIA: every series is current")
            return
    _run("sync-eia", ids, lambda s, i: ingest.sync_eia(s, eia, i))


@app.command()
def sync_cot(
    since: Annotated[
        str | None, typer.Option(help="YYYY-MM-DD; default: 2006 on first run, else 4 weeks back")
    ] = None,
) -> None:
    """Load CFTC Commitments of Traders (legacy, disaggregated and financial futures) for
    the curated markets in cot.py. Recent weeks are re-fetched to pick up corrections."""
    from fin_intel import cot
    from fin_intel.models import CotPosition
    from fin_intel.providers import CftcProvider

    cftc = CftcProvider(raw_store=default_store())
    with session_factory()() as session:
        latest = session.scalar(select(func.max(CotPosition.report_date)))
    start = (
        date.fromisoformat(since)
        if since
        else latest - timedelta(weeks=4)
        if latest
        else date(2006, 1, 1)
    )
    _run(
        "sync-cot",
        list(cot.REPORTS),
        lambda s, r: ingest.sync_cot(s, cftc, r, start),
    )


@app.command()
def sync_prices(
    tickers: list[str],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: incremental")] = None,
) -> None:
    """Load daily prices, splits and dividends from Tiingo."""
    tiingo = TiingoProvider(raw_store=default_store())
    start_date = date.fromisoformat(start) if start else None
    _run("sync-prices", tickers, lambda s, t: ingest.sync_prices(s, tiingo, t, start_date))


@app.command("universe")
def universe_cmd(
    write: Annotated[bool, typer.Option("--write", help="Rebuild and save the selection")] = False,
    out: Annotated[Path | None, typer.Option(help="Save here instead of the package")] = None,
) -> None:
    """Show (or with --write, rebuild and save) the deep-history universe: strategic
    industries' largest companies plus benchmark ETFs (universe.py). The saved file is
    committed, so rebuild where the full database is and commit the result."""
    from fin_intel import universe

    if write:
        with session_factory()() as session:
            members = universe.build(session)
        universe.write(members, out or universe.MEMBERS_FILE)
    else:
        members = universe.read(out or universe.MEMBERS_FILE)
    counts: dict[str, int] = {}
    for m in members:
        counts[m.industry] = counts.get(m.industry, 0) + 1
    for industry, n in counts.items():
        tickers = " ".join(m.ticker for m in members if m.industry == industry)
        typer.echo(f"{industry} ({n}): {tickers}")
    typer.echo(f"total: {len(members)}")


@app.command("sync-deep-history")
def sync_deep_history(
    limit: Annotated[int | None, typer.Option(help="Most symbols this run")] = None,
) -> None:
    """Fetch full daily history from Tiingo for deep-history universe members that don't
    have it yet. Tiingo's free plan allows 500 symbols a month and 50 requests an hour, so
    a first run takes many hours; whatever the monthly cap stops resumes next run."""
    from fin_intel import universe

    tiingo = TiingoProvider(raw_store=default_store())
    with session_factory()() as session:
        done = ingest.synced_since(
            session, "tiingo", "daily_prices", datetime.min.replace(tzinfo=UTC)
        )
    pending = [m.ticker for m in universe.read() if m.ticker not in done][:limit]
    if pending:
        _run("sync-deep-history", pending, lambda s, t: ingest.sync_prices(s, tiingo, t))


def _weekdays(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5]


@app.command()
def sync_market_daily(
    since: Annotated[
        str | None, typer.Option(help="YYYY-MM-DD; default: day after the last stored day")
    ] = None,
    until: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: yesterday")] = None,
    otc: Annotated[bool, typer.Option("--otc", help="Include OTC securities")] = False,
) -> None:
    """Load whole-market daily bars from Massive, one call per trading day.

    The free plan allows 5 calls/minute and two years of history, so a full backfill
    (about 500 trading days) takes roughly 100 minutes. Holidays return no rows.
    """
    massive = MassiveProvider(raw_store=default_store())
    end = date.fromisoformat(until) if until else date.today() - timedelta(days=1)
    if since:
        start = date.fromisoformat(since)
    else:
        with session_factory()() as session:
            last = session.scalar(
                select(func.max(DailyBar.date)).where(DailyBar.source == massive.name)
            )
        start = last + timedelta(days=1) if last else end - timedelta(days=7)
    days = _weekdays(start, end)
    if not days:
        typer.echo("up to date")
        return
    _run(
        "sync-market-daily",
        [d.isoformat() for d in days],
        lambda s, d: ingest.sync_market_daily(s, massive, date.fromisoformat(d), otc),
    )


@app.command()
def sync_reference(
    otc: Annotated[
        bool, typer.Option("--otc", help="Also load OTC tickers (~20 more calls)")
    ] = False,
) -> None:
    """Load Massive's reference tickers: types and FIGIs for all, plus ETFs and funds SEC
    doesn't list. Run after sync-tickers; weekly is plenty."""
    massive = MassiveProvider(raw_store=default_store())
    markets = ["stocks", "otc"] if otc else ["stocks"]
    _run("sync-reference", markets, lambda s, m: ingest.sync_reference_tickers(s, massive, m))


@app.command()
def sync_actions(
    since: Annotated[
        str, typer.Option(help="YYYY-MM-DD; ex-dates on or after this (free plan: 2 years)")
    ] = (date.today() - timedelta(days=60)).isoformat(),
) -> None:
    """Load market-wide splits and dividends from Massive."""
    massive = MassiveProvider(raw_store=default_store())
    start = date.fromisoformat(since)
    _run(
        "sync-actions",
        ["splits", "dividends"],
        lambda s, dataset: ingest.sync_market_actions(s, massive, dataset, start),
    )


@app.command()
def sync_economic(
    series: Annotated[
        list[str] | None, typer.Argument(help="FRED ids; default: macro pack")
    ] = None,
) -> None:
    """Load FRED series with their revision history. Default: the curated macro pack
    (macro.py) or FI_FRED_SERIES."""
    fred = FredProvider(raw_store=default_store())
    ids = [s.upper() for s in series] if series else get_settings().fred_series_ids
    _run("sync-economic", ids, lambda s, i: ingest.sync_economic(s, fred, i))


def _steps(job: str, steps: list[tuple[str, Callable[[], None]]]) -> None:
    """Run each step even if an earlier one failed; fail at the end if any did."""
    failed = []
    for name, step in steps:
        typer.secho(f"== {name}", bold=True)
        try:
            step()
        except typer.Exit as exc:
            if exc.exit_code:
                failed.append(name)
    if failed:
        typer.secho(f"{job}: failed steps: {', '.join(failed)}", fg="red", err=True)
        raise typer.Exit(1)


@app.command()
def sync_daily() -> None:
    """Scheduled daily sync: market bars, recent actions, breadth, fundamentals of companies
    that filed, company metrics, FRED series, watchlist prices.

    Run after the US close (data is end-of-day). Lists come from FI_WATCHLIST and
    FI_FRED_SERIES; OTC bars follow FI_MARKET_OTC.
    """
    settings = get_settings()
    recent = (date.today() - timedelta(days=30)).isoformat()  # catches late corrections
    steps = [
        ("market bars", lambda: sync_market_daily(otc=settings.market_otc)),
        ("splits and dividends", lambda: sync_actions(since=recent)),
        ("market breadth", derive_breadth_cmd),
        ("fundamentals (SEC bulk, changed companies only)", sync_fundamentals_bulk),
        ("insider transactions", sync_insiders),
        ("congressional trades", sync_congress),
        ("economic series", lambda: sync_economic(settings.fred_series_ids)),
        ("EIA energy data", sync_eia),
        ("Japanese rates and flows (MoF)", sync_japan),
        ("options put/call (Cboe)", sync_cboe),
        ("Korean filings (DART)", lambda: sync_dart(limit=6000)),
        ("Japanese filings (EDINET)", sync_edinet),
        ("Taiwanese prices", sync_tw_prices),
        ("company metrics", derive_metrics_cmd),  # last: uses every market's new data
    ]
    if settings.watchlist_tickers:
        steps.append(("watchlist prices", lambda: sync_prices(settings.watchlist_tickers)))
    _steps("sync-daily", steps)


@app.command()
def sync_weekly() -> None:
    """Scheduled weekly sync: security lists and raw retention (fundamentals load nightly
    from SEC's bulk file in sync-daily)."""
    settings = get_settings()
    steps = [
        ("SEC tickers", sync_tickers),
        ("Massive reference", lambda: sync_reference(otc=settings.market_otc)),
        ("institutional holdings (13F)", lambda: sync_13f(files=4)),
        ("CFTC positioning", sync_cot),
        ("ADR share counts", sync_adr_shares),
        ("SIC codes of new issuers", sync_sic),
        ("corporate events (SEC filings)", sync_events),
        ("filings missing from SEC company facts", fill_xbrl_gaps),
        ("Taiwanese companies (TWSE/TPEx)", sync_twse),
        ("European annual reports (ESEF)", sync_esef),
        ("cross-listings of foreign companies", sync_crosslist),
        ("economic release calendar", sync_calendar),
        ("JGB history (MoF, monthly file)", lambda: sync_japan(history=True)),
        ("federal contract obligations (USAspending)", sync_contracts),
        ("short interest (FINRA)", sync_short_interest),
        ("index ETF holdings (N-PORT)", sync_funds),
        ("forward outcomes", derive_outcomes_cmd),
    ]
    steps.append(("raw retention", lambda: prune_raw(keep=3, min_age_days=31, dry_run=False)))
    _steps("sync-weekly", steps)


@app.command("rebuild")
def rebuild_cmd(
    target: Annotated[str, typer.Argument(help=f"One of: {', '.join(TARGETS)}")],
) -> None:
    """Rebuild tables from stored raw responses (no network). Use after parser changes."""
    if target not in TARGETS:
        raise typer.BadParameter(f"choose from {', '.join(TARGETS)}")
    with session_factory()() as session:
        for dataset, count in sorted(rebuild(session, default_store(), target).items()):
            typer.echo(f"replayed {count:>5} {dataset}")


@app.command("derive-breadth")
def derive_breadth_cmd() -> None:
    """Recompute market breadth from stored bars (no network)."""
    from fin_intel import breadth

    with session_factory()() as session:
        typer.echo(f"breadth: {breadth.compute(session)} days")


@app.command("derive-metrics")
def derive_metrics_cmd(
    as_of: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: today")] = None,
) -> None:
    """Compute company metrics from statements and prices (no network), as of today or,
    point in time, a past day."""
    from fin_intel import metrics

    day = date.fromisoformat(as_of) if as_of else None
    with session_factory()() as session:
        typer.echo(f"metrics: {metrics.compute(session, day)} companies")


@app.command("derive-outcomes")
def derive_outcomes_cmd() -> None:
    """Forward outcomes (returns, excess returns vs SPY / universe / sector, drawdowns,
    re-ratings) after every company metrics snapshot: the labels signals are scored
    against. Run after backfill-metrics; recent snapshots fill in as time passes."""
    from fin_intel import outcomes

    with session_factory()() as session:
        typer.echo(f"outcomes: {outcomes.compute(session)} rows")


@app.command("backfill-metrics")
def backfill_metrics_cmd(
    start: Annotated[str, typer.Option(help="YYYY-MM-DD")],
    end: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: last month end")] = None,
) -> None:
    """Point-in-time metrics on each month's last trading day (for screen backtests)."""
    from fin_intel import metrics, timeseries

    with session_factory()() as session:
        days = timeseries.calendar(
            session, date.fromisoformat(start), date.fromisoformat(end) if end else None
        )
        month_ends = [d for d, nxt in zip(days, days[1:], strict=False) if d.month != nxt.month]
        for day in month_ends:
            typer.echo(f"{day}: {metrics.compute(session, day)} companies")


@app.command("derive")
def derive_cmd() -> None:
    """Recompute fiscal calendars and fact labels for every issuer (no network)."""
    with session_factory()() as session:
        typer.echo(f"derived {derive.derive_all(session)} issuers")


@app.command()
def prune_raw(
    keep: Annotated[int, typer.Option(help="Snapshot responses to keep per item")] = 3,
    min_age_days: Annotated[int, typer.Option(help="Never prune anything younger")] = 31,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Drop raw responses no rebuild can need (old snapshots, old errors)."""
    from datetime import timedelta

    result = prune(
        default_store(),
        SNAPSHOT_DATASETS,
        keep=keep,
        min_age=timedelta(days=min_age_days),
        dry_run=dry_run,
    )
    verb = "would remove" if dry_run else "removed"
    typer.echo(
        f"{verb} {result.responses} responses, {result.files} files "
        f"({result.bytes / 1_048_576:.1f} MB)"
    )


@app.command()
def migrate() -> None:
    """Apply pending database migrations (every command does this first; deploys call it
    explicitly before restarting the API)."""
    typer.echo("database schema is up to date")


@app.command()
def make_migration(message: str) -> None:
    """Generate a migration from changes to models.py (review it before committing)."""
    from alembic import command

    from fin_intel.db import MIGRATIONS, alembic_config, get_engine

    rev_id = f"{len(list((MIGRATIONS / 'versions').glob('[0-9]*.py'))) + 1:04d}"
    with get_engine().begin() as connection:
        command.revision(
            alembic_config(connection), message=message, autogenerate=True, rev_id=rev_id
        )


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("fin_intel.api:app", host=host, port=port, reload=reload)


# --- research -------------------------------------------------------------------------


@app.command("timeseries")
def timeseries_cmd(
    specs: Annotated[list[str], typer.Argument(help="e.g. 'px:SPY|sma:200' 'fred:T10Y2Y'")],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    end: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    pit: Annotated[bool, typer.Option(help="FRED values as known on each date")] = True,
    csv_path: Annotated[str | None, typer.Option("--csv", help="Write all rows to a CSV")] = None,
    rows: Annotated[int, typer.Option(help="Rows to print")] = 10,
) -> None:
    """Prices, macro and indicators on the trading calendar (see timeseries.py for syntax)."""
    import csv

    from fin_intel import timeseries

    with session_factory()() as session:
        try:
            dates, series = timeseries.build(
                session,
                specs,
                date.fromisoformat(start) if start else None,
                date.fromisoformat(end) if end else None,
                pit,
            )
        except timeseries.SpecError as exc:
            raise typer.BadParameter(str(exc)) from None
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", *series])
            for i, d in enumerate(dates):
                writer.writerow([d.isoformat(), *(v[i] for v in series.values())])
        typer.echo(f"wrote {len(dates)} rows to {csv_path}")
    width = max(12, *(len(k) for k in series))
    typer.echo(f"{'date':<11}" + "".join(f"{k:>{width + 2}}" for k in series))
    for i in range(max(0, len(dates) - rows), len(dates)):
        cells = "".join(
            f"{'—' if v[i] is None else f'{v[i]:.4g}':>{width + 2}}" for v in series.values()
        )
        typer.echo(f"{dates[i].isoformat():<11}{cells}")


@app.command("backtest")
def backtest_cmd(
    asset: Annotated[str, typer.Argument(help="Ticker traded, e.g. SPY")],
    rule: Annotated[str, typer.Argument(help="e.g. 'px:SPY > px:SPY|sma:200'")],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    end: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    cost_bps: Annotated[float, typer.Option(help="Cost per unit traded, basis points")] = 5.0,
    short: Annotated[bool, typer.Option(help="Short when the rule is false")] = False,
    csv_path: Annotated[str | None, typer.Option("--csv", help="Write the equity curve")] = None,
) -> None:
    """Backtest a long/flat (or long/short) rule on point-in-time series (see backtest.py)."""
    import csv

    from fin_intel import backtest

    with session_factory()() as session:
        try:
            r = backtest.run(
                session,
                asset,
                rule,
                date.fromisoformat(start) if start else None,
                date.fromisoformat(end) if end else None,
                cost_bps,
                short,
            )
        except backtest.RuleError as exc:
            raise typer.BadParameter(str(exc)) from None

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v:+.1%}"

    typer.echo(f"{r.asset}: {r.rule}")
    typer.echo(f"{r.start} to {r.end} ({r.years:.1f} years), exposure {r.exposure:.0%}")
    typer.echo(f"{'':12}{'strategy':>10}{'buy&hold':>10}")
    for label, key in [
        ("total", "total_return"),
        ("CAGR", "cagr"),
        ("volatility", "volatility"),
        ("max DD", "max_drawdown"),
    ]:
        a, b = getattr(r.strategy, key), getattr(r.benchmark, key)
        typer.echo(f"{label:12}{pct(a):>10}{pct(b):>10}")
    sharpe = [s.sharpe for s in (r.strategy, r.benchmark)]
    typer.echo(
        f"{'Sharpe':12}" + "".join(f"{'—' if s is None else f'{s:.2f}':>10}" for s in sharpe)
    )
    typer.echo(
        f"trades {r.trades}, win rate {pct(r.win_rate).lstrip('+')}, avg trade {pct(r.avg_trade)}"
    )
    typer.echo(
        "by year: "
        + "  ".join(f"{y['year']} {pct(y['strategy'])}/{pct(y['benchmark'])}" for y in r.by_year)
    )
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", "strategy", "benchmark"])
            for d, e, b in zip(r.dates, r.equity, r.benchmark_equity, strict=True):
                writer.writerow([d.isoformat(), e, b])
        typer.echo(f"wrote {len(r.dates)} rows to {csv_path}")


@app.command("event-study")
def event_study_cmd(
    source: Annotated[str, typer.Argument(help="insiders, congress or 13f")],
    member: Annotated[str | None, typer.Option(help="congress: part of a name")] = None,
    min_amount: Annotated[float, typer.Option(help="congress: minimum reported amount")] = 0,
    cik: Annotated[int | None, typer.Option(help="13f: one filer")] = None,
    min_insiders: Annotated[int, typer.Option(help="insiders: cluster size")] = 3,
    item: Annotated[str | None, typer.Option(help="8k: item, e.g. 2.02 (earnings)")] = None,
    form: Annotated[str | None, typer.Option(help="8k: form, e.g. 'SC 13D'")] = None,
) -> None:
    """Average returns vs SPY after disclosed trades or corporate events (from the next
    trading day): insiders, congress, 13f, 8k."""
    from fin_intel import events

    with session_factory()() as session:
        try:
            found = events.from_source(
                session, source, member, min_amount, cik, min_insiders, item, form
            )
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from None
        result = events.study(session, found)
    typer.echo(f"{result.events} events, {result.priced} with prices")
    typer.echo(f"{'horizon':>8}{'n':>7}{'excess':>9}{'median':>9}{'hit':>7}{'t':>7}{'return':>9}")
    for h in result.horizons:
        if not h.n:
            continue
        typer.echo(
            f"{h.horizon:>7}d{h.n:>7}{h.mean_excess:>+9.2%}{h.median_excess:>+9.2%}"
            f"{h.hit_rate:>7.0%}{h.t_stat or 0:>7.1f}{h.mean_return:>+9.2%}"
        )


@app.command("screen-backtest")
def screen_backtest_cmd(
    preset: Annotated[
        str | None, typer.Option(help="magic, deep_value, quality, cash_cows")
    ] = None,
    where: Annotated[list[str] | None, typer.Option("--where", "-w")] = None,
    sort: Annotated[str | None, typer.Option(help="metric, '-' for descending")] = None,
    rank: Annotated[str | None, typer.Option(help="'magic'")] = None,
    top: Annotated[int, typer.Option(help="Names held each period")] = 20,
    periods: Annotated[bool, typer.Option(help="Show each period's picks and returns")] = False,
) -> None:
    """Backtest a screen on point-in-time metrics (run backfill-metrics first): its top
    names vs SPY and vs the whole universe, 3, 6 and 12 months after each date."""
    from fin_intel import screener, screentest

    with session_factory()() as session:
        try:
            r = screentest.run(session, preset, where, rank, sort, top)
        except screener.ScreenError as exc:
            raise typer.BadParameter(str(exc)) from None

    def pct(v: float | None) -> str:
        return "—" if v is None else f"{v:+.1%}"

    typer.echo(f"{r.screen}: top {r.top}, {len(r.periods)} rebalance dates")
    typer.echo(
        f"{'horizon':>8}{'periods':>9}{'return':>9}{'vs univ':>9}{'vs SPY':>9}{'beat':>7}"
        f"{'median vs univ':>16}"
    )
    for s in r.summary:
        beat = "—" if s.beat_universe is None else f"{s.beat_universe:.0%}"
        typer.echo(
            f"{s.horizon:>7}d{s.periods:>9}{pct(s.mean_return):>9}{pct(s.vs_universe):>9}"
            f"{pct(s.vs_spy):>9}{beat:>7}{pct(s.median_vs_universe):>16}"
        )
    if periods:
        h = screentest.HORIZONS[0]
        for p in r.periods:
            typer.echo(
                f"{p.as_of} {pct(p.returns[h]):>8} univ {pct(p.universe[h]):>8} "
                f"SPY {pct(p.spy[h]):>8}  {' '.join(p.picks[:12])}"
            )


@app.command("screen")
def screen_cmd(
    where: Annotated[list[str] | None, typer.Option("--where", "-w", help="e.g. 'pe<15'")] = None,
    sort: Annotated[str | None, typer.Option(help="metric, '-' prefix for descending")] = None,
    rank: Annotated[str | None, typer.Option(help="'magic'")] = None,
    preset: Annotated[
        str | None, typer.Option(help="magic, deep_value, quality, cash_cows")
    ] = None,
    limit: int = 25,
    show: Annotated[
        str, typer.Option(help="Comma-separated metrics to display")
    ] = "market_cap,pe,ev_ebit,p_fcf,roic,operating_margin,revenue_growth,piotroski_f",
    sector: Annotated[list[str] | None, typer.Option(help="Only these sectors")] = None,
    exclude_sector: Annotated[list[str] | None, typer.Option(help="Leave out sectors")] = None,
) -> None:
    """Screen the market on company metrics (see screener.py; sectors in sectors.py)."""
    from fin_intel import screener

    with session_factory()() as session:
        try:
            as_of, rows = screener.screen(
                session, where, sort, rank, preset, limit, sector, exclude_sector
            )
        except screener.ScreenError as exc:
            raise typer.BadParameter(str(exc)) from None
    columns = [c.strip() for c in show.split(",") if c.strip()]
    typer.echo(f"{len(rows)} companies (metrics as of {as_of})")
    typer.echo(f"{'ticker':<8} {'name':<30}" + "".join(f"{c[:14]:>15}" for c in columns))

    def cell(value) -> str:
        if value is None:
            return "—"
        if abs(value) >= 1e9:
            return f"{value / 1e9:,.1f}B"
        if abs(value) >= 1e6:
            return f"{value / 1e6:,.1f}M"
        return f"{value:.3g}"

    for r in rows:
        name = (r["name"] or "")[:30]
        typer.echo(
            f"{r['ticker'] or '':<8} {name:<30}" + "".join(f"{cell(r[c]):>15}" for c in columns)
        )


# --- portfolio -------------------------------------------------------------------------

portfolio_app = typer.Typer(
    help="Your accounts, tax lots and tax-loss harvesting", no_args_is_help=True
)
app.add_typer(portfolio_app, name="portfolio")

ACCOUNT_TYPES = {"taxable": True, "ira": False, "roth_ira": False, "401k": False, "hsa": False}


def _money(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


@portfolio_app.command("add-account")
def portfolio_add_account(
    name: str,
    broker: Annotated[str, typer.Option(help="fidelity, vanguard, manual, ...")],
    account_type: Annotated[str, typer.Option("--type", help=", ".join(ACCOUNT_TYPES))],
    last4: Annotated[str | None, typer.Option(help="Last 4 digits of the account number")] = None,
) -> None:
    """Register an account. Only taxable accounts are harvesting candidates."""
    from fin_intel.models import Account

    if account_type not in ACCOUNT_TYPES:
        raise typer.BadParameter(f"--type must be one of {', '.join(ACCOUNT_TYPES)}")
    with session_factory()() as session:
        session.add(
            Account(
                name=name,
                broker=broker,
                account_type=account_type,
                taxable=ACCOUNT_TYPES[account_type],
                number_last4=last4,
            )
        )
        session.commit()
    typer.echo(f"added {name} ({broker}, {account_type})")


@portfolio_app.command("accounts")
def portfolio_accounts() -> None:
    from fin_intel.models import Account

    with session_factory()() as session:
        for a in session.scalars(select(Account).order_by(Account.name)):
            tax = "taxable" if a.taxable else "tax-advantaged"
            typer.echo(
                f"{a.name:<28} {a.broker:<10} {a.account_type:<9} {tax:<15} {a.number_last4 or ''}"
            )


@portfolio_app.command("import")
def portfolio_import(
    path: str,
    fmt: Annotated[
        str, typer.Option("--format", help="generic (Fidelity/Vanguard to come)")
    ] = "generic",
) -> None:
    """Import a transactions export. Re-importing overlapping exports is safe."""
    from fin_intel import importers

    reader = importers.TRANSACTION_FORMATS.get(fmt)
    if reader is None:
        raise typer.BadParameter(
            f"--format must be one of {', '.join(importers.TRANSACTION_FORMATS)}"
        )
    with session_factory()() as session:
        try:
            new = importers.load_transactions(session, reader(path), source=fmt)
        except importers.PortfolioImportError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from None
    typer.echo(f"{new} new transactions")


@portfolio_app.command("import-positions")
def portfolio_import_positions(
    path: str,
    fmt: Annotated[str, typer.Option("--format", help="generic or fidelity")] = "generic",
) -> None:
    """Import a positions snapshot. Accounts without transaction history are reported from
    it; otherwise it reconciles derived lots and prices funds without market data."""
    from fin_intel import importers

    if fmt not in importers.POSITION_FORMATS:
        raise typer.BadParameter(f"--format must be one of {', '.join(importers.POSITION_FORMATS)}")
    with session_factory()() as session:
        try:
            if fmt == "fidelity":
                rows, accounts = importers.read_fidelity_positions(path)
                for name in importers.ensure_accounts(session, accounts, "fidelity"):
                    account_type, last4 = accounts[name]
                    typer.echo(f"created account {name} ({account_type}, ...{last4})")
            else:
                rows = importers.POSITION_FORMATS[fmt](path)
            count = importers.load_positions(session, rows)
        except importers.PortfolioImportError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from None
    typer.echo(f"{count} positions")


@portfolio_app.command("positions")
def portfolio_positions() -> None:
    """Holdings with cost, value and unrealized gain (short/long term)."""
    from fin_intel import portfolio

    with session_factory()() as session:
        rows = portfolio.positions(session)
        warnings = portfolio.lot_book(session).warnings
    typer.echo(
        f"{'account':<26} {'symbol':<8} {'shares':>12} {'cost':>14} {'value':>14} "
        f"{'unrealized':>13} {'short':>12} {'long':>12}  check"
    )
    for p in rows:
        if p.basis_source == "broker":
            check = "broker basis"
        elif p.reconciled is None:
            check = ""
        else:
            check = "ok" if p.reconciled else f"broker: {p.broker_quantity:g}"
        typer.echo(
            f"{p.account:<26} {p.symbol:<8} {p.quantity:>12,.4f} {_money(p.cost_basis):>14} "
            f"{_money(p.market_value):>14} {_money(p.unrealized):>13} "
            f"{_money(p.unrealized_short):>12} {_money(p.unrealized_long):>12}  {check}"
        )
    for w in warnings:
        typer.secho(f"warning: {w}", fg="yellow", err=True)


@portfolio_app.command("harvest")
def portfolio_harvest(
    min_loss: Annotated[float, typer.Option(help="Minimum loss in dollars")] = 0.0,
    min_loss_pct: Annotated[float, typer.Option(help="Minimum loss as a fraction, e.g. 0.1")] = 0.0,
) -> None:
    """Taxable lots with unrealized losses, and what would wash the loss."""
    from fin_intel import portfolio

    with session_factory()() as session:
        candidates = portfolio.harvest_candidates(session, min_loss, min_loss_pct)
    if not candidates:
        typer.echo("no harvesting candidates")
    for c in candidates:
        lot = c.lot
        held = (
            f"acquired {lot.acquired} ({lot.term}-term)"
            if lot.acquired
            else "holding period unknown"
        )
        typer.echo(
            f"{lot.account:<26} {lot.symbol:<8} {lot.quantity:>10,.4f} sh {held}: "
            f"loss {_money(c.loss)} ({c.loss_pct:.1%})"
        )
        if not c.history_known:
            typer.secho(
                "    no transaction history: can't check purchases in the last 30 days "
                "(including reinvested dividends)",
                fg="yellow",
            )
        for account, day, shares in c.blocking_purchases:
            typer.secho(f"    would wash: bought {shares:g} on {day} in {account}", fg="yellow")
        typer.echo(f"    don't buy it back before {c.rebuy_after}")


@portfolio_app.command("realized")
def portfolio_realized(year: Annotated[int | None, typer.Option(help="Tax year")] = None) -> None:
    """Realized gains and losses (FIFO), split short/long term."""
    from fin_intel import portfolio

    with session_factory()() as session:
        realized = [
            r for r in portfolio.lot_book(session).realized if year is None or r.sold.year == year
        ]
    totals: dict[str, float] = {"short": 0.0, "long": 0.0}
    for r in realized:
        flag = "  possible wash sale" if r.wash_sale_risk else ""
        typer.echo(
            f"{r.sold} {r.symbol:<8} {r.quantity:>10,.4f} sh  acquired {r.acquired or '?'}  "
            f"{r.term or '?':<5} gain {_money(r.gain)}{flag}"
        )
        if r.gain is not None and r.term:
            totals[r.term] += r.gain
    typer.echo(f"short-term {_money(totals['short'])}   long-term {_money(totals['long'])}")


@portfolio_app.command("prices")
def portfolio_prices() -> None:
    """Fetch Tiingo prices for holdings without recent market data (e.g. mutual funds)."""
    from fin_intel import portfolio

    with session_factory()() as session:
        held = {lot.symbol for lot in portfolio.lot_book(session).open}
        priced = portfolio.latest_prices(session, held)
    stale = date.today() - timedelta(days=7)
    missing = sorted(
        s for s in held if s not in priced or priced[s][2] == "broker" or priced[s][1] < stale
    )
    if not missing:
        typer.echo("all holdings have recent prices")
        return
    sync_prices(missing)
