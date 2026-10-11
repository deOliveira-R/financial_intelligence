"""US listings and market data: security lists, daily bars, splits and dividends,
ADR share counts, the deep-history universe."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import func, select

from fin_intel import (
    market,
    pipeline,
)
from fin_intel.cli.app import app, run_items, weekdays
from fin_intel.db import session_factory
from fin_intel.models import DailyBar
from fin_intel.providers import (
    MassiveProvider,
    SecProvider,
    TiingoProvider,
)
from fin_intel.raw import default_store


@app.command(rich_help_panel="Market data")
def sync_tickers() -> None:
    """Load all SEC-registered tickers and their CIKs."""
    sec = SecProvider(raw_store=default_store())
    run_items("sync-tickers", ["all"], lambda s, _: market.sync_tickers(s, sec))


@app.command(rich_help_panel="Market data")
def sync_reference(
    otc: Annotated[
        bool, typer.Option("--otc", help="Also load OTC tickers (~20 more calls)")
    ] = False,
) -> None:
    """Load Massive's reference tickers: types and FIGIs for all, plus ETFs and funds SEC
    doesn't list. Run after sync-tickers; weekly is plenty."""
    massive = MassiveProvider(raw_store=default_store())
    markets = ["stocks", "otc"] if otc else ["stocks"]
    run_items("sync-reference", markets, lambda s, m: market.sync_reference_tickers(s, massive, m))


@app.command(rich_help_panel="Market data")
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
    days = weekdays(start, end)
    if not days:
        typer.echo("up to date")
        return
    run_items(
        "sync-market-daily",
        [d.isoformat() for d in days],
        lambda s, d: market.sync_market_daily(s, massive, date.fromisoformat(d), otc),
    )


@app.command(rich_help_panel="Market data")
def sync_actions(
    since: Annotated[
        str, typer.Option(help="YYYY-MM-DD; ex-dates on or after this (free plan: 2 years)")
    ] = (date.today() - timedelta(days=60)).isoformat(),
) -> None:
    """Load market-wide splits and dividends from Massive."""
    massive = MassiveProvider(raw_store=default_store())
    start = date.fromisoformat(since)
    run_items(
        "sync-actions",
        ["splits", "dividends"],
        lambda s, dataset: market.sync_market_actions(s, massive, dataset, start),
    )


@app.command(rich_help_panel="Market data")
def sync_prices(
    tickers: list[str],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: incremental")] = None,
) -> None:
    """Load daily prices, splits and dividends from Tiingo."""
    tiingo = TiingoProvider(raw_store=default_store())
    start_date = date.fromisoformat(start) if start else None
    run_items("sync-prices", tickers, lambda s, t: market.sync_prices(s, tiingo, t, start_date))


@app.command(rich_help_panel="Market data")
def sync_adr_shares(
    limit: Annotated[int, typer.Option(help="Most ADRs to refresh this run")] = 150,
) -> None:
    """Refresh ADRs' depositary share counts from Massive (for their market caps), oldest
    first; each is refreshed every four weeks."""
    massive = MassiveProvider(raw_store=default_store())
    with session_factory()() as session:
        tickers = market.due_listing_shares(session, limit=limit)
    if tickers:
        run_items(
            "sync-adr-shares", tickers, lambda s, t: market.sync_listing_shares(s, massive, t)
        )


@app.command("universe", rich_help_panel="Market data")
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


@app.command("sync-deep-history", rich_help_panel="Market data")
def sync_deep_history(
    limit: Annotated[int | None, typer.Option(help="Most symbols this run")] = None,
) -> None:
    """Fetch full daily history from Tiingo for deep-history universe members that don't
    have it yet. Tiingo's free plan allows 500 symbols a month and 50 requests an hour, so
    a first run takes many hours; whatever the monthly cap stops resumes next run."""
    from fin_intel import universe

    tiingo = TiingoProvider(raw_store=default_store())
    # Wait out the hourly window (50 requests) rather than stopping; the daily cap still
    # stops the run, and the next one resumes with what's left.
    tiingo.limiter.max_wait = 3700
    with session_factory()() as session:
        done = pipeline.synced_since(
            session, "tiingo", "daily_prices", datetime.min.replace(tzinfo=UTC)
        )
    pending = [m.ticker for m in universe.read() if m.ticker not in done][:limit]
    if pending:
        run_items("sync-deep-history", pending, lambda s, t: market.sync_prices(s, tiingo, t))
