"""Derived tables: fiscal calendars and statements, breadth, company metrics (today
and monthly snapshots), forward outcomes."""

from datetime import date
from typing import Annotated

import typer

from fin_intel import (
    derive,
)
from fin_intel.cli.app import app
from fin_intel.db import session_factory


@app.command("derive-breadth", rich_help_panel="Derived tables")
def derive_breadth_cmd() -> None:
    """Recompute market breadth from stored bars (no network)."""
    from fin_intel import breadth

    with session_factory()() as session:
        typer.echo(f"breadth: {breadth.compute_all(session)} universe-days")


@app.command("derive-metrics", rich_help_panel="Derived tables")
def derive_metrics_cmd(
    as_of: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: today")] = None,
) -> None:
    """Compute company metrics from statements and prices (no network), as of today or,
    point in time, a past day."""
    from fin_intel import metrics

    day = date.fromisoformat(as_of) if as_of else None
    with session_factory()() as session:
        typer.echo(f"metrics: {metrics.compute(session, day)} companies")


@app.command("derive-outcomes", rich_help_panel="Derived tables")
def derive_outcomes_cmd() -> None:
    """Forward outcomes (returns, excess returns vs SPY / universe / sector, drawdowns,
    re-ratings) after every company metrics snapshot: the labels signals are scored
    against. Run after backfill-metrics; recent snapshots fill in as time passes."""
    from fin_intel import outcomes

    with session_factory()() as session:
        typer.echo(f"outcomes: {outcomes.compute(session)} rows")


@app.command("derive-quarterly-prices", rich_help_panel="Derived tables")
def derive_quarterly_prices_cmd(
    all_periods: Annotated[
        bool, typer.Option("--all", help="Recompute every quarter, not only new and recent ones")
    ] = False,
) -> None:
    """Quarter-end prices for every stock institutions held, from 13F values (the median of
    value/shares across filers): survivorship-free since 2001, for stock-level research."""
    from sqlalchemy import select

    from fin_intel import quarterly
    from fin_intel.models import InstitutionalPosition

    with session_factory()() as session:
        periods = (
            sorted(session.scalars(select(InstitutionalPosition.period).distinct()))
            if all_periods
            else None
        )
        typer.echo(f"quarterly prices: {quarterly.compute(session, periods)} rows")


@app.command("backfill-metrics", rich_help_panel="Derived tables")
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


@app.command("derive", rich_help_panel="Derived tables")
def derive_cmd() -> None:
    """Recompute fiscal calendars and fact labels for every issuer (no network)."""
    with session_factory()() as session:
        typer.echo(f"derived {derive.derive_all(session)} issuers")
