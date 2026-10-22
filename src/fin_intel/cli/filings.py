"""Company filings and fundamentals: SEC (bulk and per company, missing XBRL, events,
SIC codes, text), other regulators (DART, EDINET, ESEF, TWSE), cross-listings, fund holdings."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import (
    crosslist,
    dart,
    edgar,
    edinet,
    filing_text,
    funds,
    pipeline,
    taiwan,
)
from fin_intel.cli.app import app, run_items, weekdays
from fin_intel.db import session_factory
from fin_intel.models import SyncState
from fin_intel.providers import (
    SecProvider,
)
from fin_intel.raw import default_store


@app.command(rich_help_panel="Filings and fundamentals")
def sync_fundamentals(tickers: list[str]) -> None:
    """Load SEC XBRL financial facts for the given tickers."""
    sec = SecProvider(raw_store=default_store())
    run_items("sync-fundamentals", tickers, lambda s, t: edgar.sync_fundamentals(s, sec, t))


@app.command(rich_help_panel="Filings and fundamentals")
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

    from fin_intel.config import get_settings as settings

    store = default_store()
    downloaded = zip_path is None
    # Under the raw directory: the only writable, persistent path in the containers (the
    # image is read-only and /tmp is memory), and the ~1 GB download resumes from there.
    path = Path(zip_path) if zip_path else Path(settings().raw_dir) / "tmp" / "companyfacts.zip"

    def work(session: Session, _: str) -> int:
        if downloaded:
            typer.echo("downloading companyfacts.zip ...")
            SecProvider(raw_store=store).download_bulk_company_facts(path)
        ciks = None if all_filers else pipeline.tracked_ciks(session)

        def progress(n: int, total: int) -> None:
            if n % 250 == 0 or n == total:
                typer.echo(f"  {n}/{total} companies")

        loaded, unchanged = edgar.load_bulk_company_facts(session, store, path, ciks, progress)
        typer.echo(f"loaded {loaded}, unchanged {unchanged}")
        return loaded

    try:
        run_items("sync-fundamentals-bulk", ["bulk"], work)
    finally:
        if downloaded:
            path.unlink(missing_ok=True)


@app.command("fill-xbrl-gaps", rich_help_panel="Filings and fundamentals")
def fill_xbrl_gaps(
    cik: Annotated[list[int] | None, typer.Option(help="Only these issuers")] = None,
) -> None:
    """Read recent periodic reports' XBRL straight from the filings for issuers whose
    financials are stale (SEC's company facts can lag, e.g. new IFRS taxonomies)."""
    sec = SecProvider(raw_store=default_store())
    with session_factory()() as session:
        ciks = cik or edgar.stale_issuers(session)
    typer.echo(f"{len(ciks)} issuers with stale financials")
    if ciks:
        run_items(
            "fill-xbrl-gaps",
            [str(c) for c in ciks],
            lambda s, c: edgar.sync_xbrl_gaps(s, sec, int(c)),
        )


@app.command("sync-events", rich_help_panel="Filings and fundamentals")
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
    sec = SecProvider(raw_store=default_store())
    with session_factory()() as session:
        ciks = cik or edgar.sec_filers(session)
        if not cik:
            fresh = pipeline.synced_since(
                session, "sec", "events", datetime.now(UTC) - timedelta(days=refresh_days)
            )
            ciks = [c for c in ciks if str(c) not in fresh]
    if ciks:
        run_items(
            "sync-events", [str(c) for c in ciks], lambda s, c: edgar.sync_events(s, sec, int(c))
        )


@app.command("sync-population", rich_help_panel="Filings and fundamentals")
def sync_population() -> None:
    """The research population (every company that filed financial statements since 2009,
    dead or alive; population.py): EDGAR's quarterly XBRL indexes, then the events of
    members never synced (their fundamentals come with sync-fundamentals-bulk). Members
    no longer filing don't change, so each is fetched once."""
    from fin_intel import population

    sec = SecProvider(raw_store=default_store())
    with session_factory()() as session:
        rows = population.sync_population(session, sec)
        typer.echo(f"{rows} index rows")
        synced = pipeline.synced_since(session, "sec", "events", datetime(2000, 1, 1, tzinfo=UTC))
        ciks = sorted(c for c in population.members(session) if str(c) not in synced)
    typer.echo(f"{len(ciks)} members without events")
    if ciks:
        run_items(
            "sync-events", [str(c) for c in ciks], lambda s, c: edgar.sync_events(s, sec, int(c))
        )


@app.command(rich_help_panel="Filings and fundamentals")
def sync_sic() -> None:
    """Look up SIC codes (industry) of issuers with a primary listing that lack one (SEC
    submissions, one request each; later runs only fetch new issuers)."""
    sec = SecProvider(raw_store=default_store())
    with session_factory()() as session:
        ciks = [str(c) for c in edgar.issuers_without_sic(session)]
    if ciks:
        run_items("sync-sic", ciks, lambda s, c: edgar.sync_submissions(s, sec, int(c)))


@app.command("sync-filing-text", rich_help_panel="Filings and fundamentals")
def sync_filing_text(
    cik: Annotated[list[int] | None, typer.Option(help="Only these SEC filers")] = None,
    since: Annotated[str, typer.Option(help="Filed on or after (YYYY-MM-DD)")] = "2023-01-01",
) -> None:
    """Fetch 10-K/10-Q/20-F narrative sections (risk factors, MD&A) and press releases
    (earnings 8-Ks, 6-Ks) as searchable text, for the deep-history universe's companies (or
    --cik). Filings fetched before are skipped."""
    from fin_intel import universe

    sec = SecProvider(raw_store=default_store())
    ciks = cik or sorted({m.cik for m in universe.read() if m.cik})
    start = date.fromisoformat(since)
    run_items(
        "sync-filing-text",
        [str(c) for c in ciks],
        lambda s, c: filing_text.sync_filing_text(s, sec, int(c), start),
    )


@app.command("sync-dart", rich_help_panel="Filings and fundamentals")
def sync_dart(
    corp: Annotated[list[str] | None, typer.Option(help="Only these DART corp codes")] = None,
    limit: Annotated[
        int, typer.Option(help="Most reports this run (DART: ~20k requests/day)")
    ] = 8000,
    first_year: Annotated[int, typer.Option(help="Oldest business year to load")] = 2015,
) -> None:
    """Load Korean listed companies' financial statements from DART, newest reports first;
    each run continues where the last one stopped (within the daily request quota)."""
    from fin_intel.models import Issuer
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
        run_items("sync-dart", ["corp codes"], lambda s, _: dart.sync_dart_corps(s, provider))
    with session_factory()() as session:
        listed = session.execute(
            select(Issuer.source_id, Issuer.fiscal_month).where(
                Issuer.source == "dart", Issuer.home_ticker.is_not(None)
            )
        ).all()
    corps = sorted(c for c, _ in listed if not corp or c in corp)
    profiles = [c for c, month in listed if month is None and c in corps]
    if profiles:
        run_items("sync-dart", profiles, lambda s, c: dart.sync_dart_company(s, provider, c))
    done = {k.rsplit("|", 1)[0] for k in store.latest_hashes("dart", "statements") if k}
    periods = dart.dart_periods(date.today(), first_year)
    latest = periods[0] if periods else None
    items = [
        f"{c}|{year}|{report}"
        for year, report in periods
        for c in corps
        if f"{c}|{year}|{report}" not in done
    ][:limit]
    if items:
        run_items(
            "sync-dart",
            items,
            lambda s, item: dart.sync_dart_report(
                s,
                provider,
                item.split("|")[0],
                int(item.split("|")[1]),
                item.split("|")[2],
                shares=(int(item.split("|")[1]), item.split("|")[2]) == latest
                or item.endswith("11011"),
            ),
        )


@app.command("sync-edinet", rich_help_panel="Filings and fundamentals")
def sync_edinet(
    since: Annotated[str, typer.Option(help="First filing day, YYYY-MM-DD")] = "2024-04-01",
    until: Annotated[str | None, typer.Option(help="Last filing day; default: yesterday")] = None,
) -> None:
    """Load Japanese listed companies' annual, quarterly and half-year reports from EDINET,
    one filing day at a time, newest first; days already loaded are skipped (the last week
    is rechecked for late additions)."""
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
        run_items(
            "sync-edinet",
            days,
            lambda s, d: edinet.sync_edinet_day(s, provider, date.fromisoformat(d)),
        )


@app.command("sync-esef", rich_help_panel="Filings and fundamentals")
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
        run_items(
            "sync-esef",
            list(pending),
            lambda s, k: esef.sync_esef_report(s, provider, pending[k]),
        )


@app.command("sync-twse", rich_help_panel="Filings and fundamentals")
def sync_twse() -> None:
    """Load Taiwan's listed (TWSE) and OTC (TPEx) companies: profiles, then the latest
    quarter's income statements and balance sheets in every industry format."""
    from fin_intel.providers import TwseProvider
    from fin_intel.providers.twse import INDUSTRIES, MARKETS

    provider = TwseProvider(raw_store=default_store())
    tables = ["t187ap03"] + [f"{s}_{i}" for s in ("t187ap06", "t187ap07") for i in INDUSTRIES]
    items = [f"{m}|{t}" for m in MARKETS for t in tables]
    today = date.today()
    run_items(
        "sync-twse",
        items,
        lambda s, item: taiwan.sync_twse_table(s, provider, *item.split("|"), today),
    )


@app.command("sync-tw-prices", rich_help_panel="Filings and fundamentals")
def sync_tw_prices(
    since: Annotated[str, typer.Option(help="First trading day, YYYY-MM-DD")] = "2024-10-01",
) -> None:
    """Load Taiwanese listed (TWSE) and OTC (TPEx) companies' daily prices, one request per
    exchange and trading day; days already loaded are skipped (the last few rechecked)."""
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
        for d in reversed(weekdays(date.fromisoformat(since), end))
        for market in ("twse", "tpex")
        if f"{market}|{d.isoformat()}" not in done or (end - d).days <= 3
    ]
    if items:
        run_items(
            "sync-tw-prices",
            items,
            lambda s, item: taiwan.sync_tw_prices(
                s, provider, item.split("|")[0], date.fromisoformat(item.split("|")[1])
            ),
        )


@app.command("sync-crosslist", rich_help_panel="Filings and fundamentals")
def sync_crosslist() -> None:
    """Link companies from other regulators to their US listings (OTC ordinary lines by
    share class, ADRs by name), so they can be valued with US prices (crosslist.py)."""
    from fin_intel.providers import GleifProvider, OpenFigiProvider

    store = default_store()
    openfigi, gleif = OpenFigiProvider(raw_store=store), GleifProvider(raw_store=store)

    def work(session: Session, _: str) -> int:
        stats = crosslist.sync_crosslist(session, openfigi, gleif)
        typer.echo(", ".join(f"{k} {v}" for k, v in stats.items()))
        return sum(stats.values())

    run_items("sync-crosslist", ["all"], work)


@app.command("sync-funds", rich_help_panel="Filings and fundamentals")
def sync_funds(
    fund: Annotated[list[str] | None, typer.Option(help="Only these fund tickers")] = None,
) -> None:
    """Load index ETF holdings (N-PORT reports since 2019) for the tracked funds in
    funds.py: point-in-time index membership and weights."""
    sec = SecProvider(raw_store=default_store())
    series = funds.fund_series(sec)
    tickers = [t.upper() for t in fund] if fund else sorted(series)
    run_items("sync-funds", tickers, lambda s, t: funds.sync_fund(s, sec, t, series[t]))
