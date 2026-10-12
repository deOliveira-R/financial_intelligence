"""Big players and policy: insiders, 13F, Congress (trades, members), lobbying,
bills, presidential documents, short interest."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

import typer
from sqlalchemy import func, select

from fin_intel import (
    congress_members,
    insiders,
    lobbying,
    pipeline,
    shortinterest,
    thirteenf,
)
from fin_intel.cli.app import app, run_items, weekdays
from fin_intel.db import session_factory
from fin_intel.models import CongressReport, SyncState
from fin_intel.providers import (
    SecProvider,
)
from fin_intel.raw import default_store


@app.command(rich_help_panel="Big players and policy")
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
        run_items(
            "sync-insiders",
            pending,
            lambda s, q: insiders.sync_insider_dataset(s, sec, q, datasets[q]),
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
    days = [d.isoformat() for d in weekdays(start, end) if d.isoformat() not in done]
    if days:
        run_items(
            "sync-insiders",
            days,
            lambda s, d: insiders.sync_insider_day(s, sec, date.fromisoformat(d)),
        )


@app.command("sync-13f", rich_help_panel="Big players and policy")
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
        run_items(
            "sync-13f", pending, lambda s, p: thirteenf.sync_13f_dataset(s, sec, p, available[p])
        )
    openfigi = OpenFigiProvider(raw_store=store)

    def progress(done: int, total: int) -> None:
        if done % 1000 < openfigi.batch or done == total:
            typer.echo(f"  mapped {done}/{total} CUSIPs")

    run_items(
        "sync-13f", ["cusips"], lambda s, _: thirteenf.sync_cusip_mappings(s, openfigi, progress)
    )


@app.command(rich_help_panel="Big players and policy")
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
        run_items("sync-congress", years, lambda s, y: congress.sync_house_index(s, house, int(y)))
        with session_factory()() as session:
            pending = {
                r.doc_id: r.year or (r.filed.year if r.filed else today.year)
                for r in congress.pending_reports(session, "house")
                if (r.year or first) >= first
            }
        if pending:
            run_items(
                "sync-congress",
                list(pending),
                lambda s, d: congress.sync_house_ptr(s, house, d, pending[d]),
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
        run_items(
            "sync-congress",
            [start.isoformat()],
            lambda s, d: congress.sync_senate_index(s, senate, date.fromisoformat(d)),
        )
        with session_factory()() as session:
            ids = [r.doc_id for r in congress.pending_reports(session, "senate")]
        if ids:
            run_items("sync-congress", ids, lambda s, d: congress.sync_senate_ptr(s, senate, d))
    from fin_intel import congress_members

    with session_factory()() as session:  # new reports to their members (no network)
        congress_members.link(session)
        session.commit()


@app.command("sync-legislators", rich_help_panel="Big players and policy")
def sync_legislators() -> None:
    """Load members of Congress (terms since 1789), current committees and assignments,
    and link each trading report to the member who filed it."""
    from fin_intel.providers import LegislatorsProvider

    provider = LegislatorsProvider(raw_store=default_store())
    run_items(
        "sync-legislators", ["all"], lambda s, _: congress_members.sync_legislators(s, provider)
    )


@app.command("sync-lobbying", rich_help_panel="Big players and policy")
def sync_lobbying(
    since: Annotated[int, typer.Option(help="First year to load (quarterly since 2008)")] = 2015,
) -> None:
    """Load lobbying reports (Senate LDA) quarter by quarter; recent quarters are refetched
    until late filings settle. Set FI_LDA_API_KEY (free) or this crawls."""
    from fin_intel.providers import LdaProvider

    lda = LdaProvider(raw_store=default_store())
    with session_factory()() as session:
        quarters = lobbying.lobbying_quarters_due(session, since, date.today())
    if quarters:
        run_items(
            "sync-lobbying",
            [f"{y}-Q{q}" for y, q in quarters],
            lambda s, k: lobbying.sync_lobbying_quarter(s, lda, int(k[:4]), int(k[-1])),
        )


@app.command("sync-conflict", rich_help_panel="Big players and policy")
def sync_conflict() -> None:
    """Load conflict events worldwide: UCDP organized violence (yearly dataset since 1989,
    monthly releases for the current year), and ACLED (also protests; month by month since
    2018, recent months re-read) when FI_ACLED_EMAIL and FI_ACLED_PASSWORD are set for a
    Research-tier account."""
    from fin_intel import conflict
    from fin_intel.config import get_settings
    from fin_intel.providers import AcledProvider, UcdpProvider

    ucdp = UcdpProvider(raw_store=default_store())
    run_items("sync-ucdp", ["all"], lambda s, _: conflict.sync_ucdp(s, ucdp, date.today()))
    if not get_settings().acled_password:
        typer.echo("ACLED account not configured (FI_ACLED_EMAIL, FI_ACLED_PASSWORD); skipping")
        return
    acled = AcledProvider(raw_store=default_store())
    with session_factory()() as session:
        months = conflict.wanted(session, date.today())
    if months:
        run_items(
            "sync-conflict",
            [f"{m:%Y-%m}" for m in months],
            lambda s, k: conflict.sync_month(s, acled, date.fromisoformat(f"{k}-01")),
        )


@app.command("sync-news", rich_help_panel="Big players and policy")
def sync_news(
    query: Annotated[list[str] | None, typer.Option(help="Only these queries (news.py)")] = None,
) -> None:
    """Load daily worldwide news attention (GDELT, 65 languages, since 2017) for the thesis
    queries in news.py: one request every 30 seconds (GDELT throttles), past years once per
    query text."""
    from fin_intel import news
    from fin_intel.providers import GdeltProvider

    gdelt = GdeltProvider(raw_store=default_store())
    names = query or list(news.QUERIES)
    unknown = set(names) - set(news.QUERIES)
    if unknown:
        raise typer.BadParameter(f"unknown queries {sorted(unknown)}")
    with session_factory()() as session:
        news.drop_unknown(session)
        session.commit()
    run_items("sync-news", names, lambda s, n: news.sync_query(s, gdelt, n, date.today()))


@app.command("sync-bills", rich_help_panel="Big players and policy")
def sync_bills(
    since: Annotated[int, typer.Option(help="First congress (112 = 2011-2012)")] = 112,
) -> None:
    """Load bills and joint resolutions (GovInfo Bill Status bulk files): past congresses
    once, the current one on every run."""
    from fin_intel import bills
    from fin_intel.providers import GovinfoProvider

    govinfo = GovinfoProvider(raw_store=default_store())
    current = bills.congress_for(date.today())
    with session_factory()() as session:
        done = pipeline.synced_since(
            session, "govinfo", "billstatus", datetime(2000, 1, 1, tzinfo=UTC)
        )
    congresses = [str(c) for c in range(since, current + 1) if str(c) not in done or c == current]
    run_items("sync-bills", congresses, lambda s, c: bills.sync_bills(s, govinfo, int(c)))


@app.command("sync-presidential", rich_help_panel="Big players and policy")
def sync_presidential(
    since: Annotated[int, typer.Option(help="First year (the API starts in 1994)")] = 1994,
) -> None:
    """Load presidential documents (executive orders, proclamations, memoranda,
    determinations, notices) with full text: past years once, this year on every run."""
    from fin_intel import presidential
    from fin_intel.providers import FederalRegisterProvider

    provider = FederalRegisterProvider(raw_store=default_store())
    this_year = date.today().year
    with session_factory()() as session:
        done = pipeline.synced_since(
            session, "federalregister", "presidential_list", datetime(2000, 1, 1, tzinfo=UTC)
        )
    years = [
        str(y)
        for y in range(max(since, presidential.FIRST_YEAR), this_year + 1)
        if str(y) not in done or y >= this_year - 1
    ]
    run_items(
        "sync-presidential",
        years,
        lambda s, y: presidential.sync_presidential_year(s, provider, int(y)),
    )


@app.command("sync-short-interest", rich_help_panel="Big players and policy")
def sync_short_interest() -> None:
    """Load FINRA short interest (every stock, twice a month since December 2017): new
    settlement dates, and recent ones again until revisions settle."""
    from fin_intel.providers import FinraProvider

    finra = FinraProvider(raw_store=default_store())
    with session_factory()() as session:
        dates = shortinterest.short_interest_due(session, finra, date.today())
    if dates:
        run_items(
            "sync-short-interest",
            [d.isoformat() for d in dates],
            lambda s, d: shortinterest.sync_short_interest(s, finra, date.fromisoformat(d)),
        )
