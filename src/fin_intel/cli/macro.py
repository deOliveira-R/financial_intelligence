"""Macro and markets data: FRED, EIA, CFTC positioning, the release calendar, Japan's
rates and flows, Cboe put/call, federal contracts."""

from datetime import date, timedelta
from typing import Annotated

import typer
from sqlalchemy import func, select

from fin_intel import (
    contracts,
    japan,
    macro,
    putcall,
    releases,
)
from fin_intel.cli.app import app, run_items
from fin_intel.config import get_settings
from fin_intel.db import session_factory
from fin_intel.providers import (
    FredProvider,
)
from fin_intel.raw import default_store


@app.command(rich_help_panel="Macro and markets")
def sync_economic(
    series: Annotated[
        list[str] | None, typer.Argument(help="FRED ids; default: macro pack")
    ] = None,
) -> None:
    """Load FRED series with their revision history. Default: the curated macro pack
    (macro.py) or FI_FRED_SERIES."""
    fred = FredProvider(raw_store=default_store())
    ids = [s.upper() for s in series] if series else get_settings().fred_series_ids
    run_items("sync-economic", ids, lambda s, i: macro.sync_economic(s, fred, i))


@app.command(rich_help_panel="Macro and markets")
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
    run_items("sync-eia", ids, lambda s, i: energy.sync_eia(s, eia, i))


@app.command(rich_help_panel="Macro and markets")
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
    run_items(
        "sync-cot",
        list(cot.REPORTS),
        lambda s, r: cot.sync_cot(s, cftc, r, start),
    )


@app.command(rich_help_panel="Macro and markets")
def sync_calendar() -> None:
    """Load the economic release calendar for the tracked FRED series (plus FOMC and Bank
    of Japan meetings)."""
    from fin_intel.providers import BojProvider, FedProvider

    store = default_store()
    fred, fed = FredProvider(raw_store=store), FedProvider(raw_store=store)
    boj = BojProvider(raw_store=store)
    run_items(
        "sync-calendar", ["all"], lambda s, _: releases.sync_release_calendar(s, fred, fed, boj)
    )


@app.command("sync-japan", rich_help_panel="Macro and markets")
def sync_japan(
    history: Annotated[bool, typer.Option(help="Also reload the JGB history file")] = False,
) -> None:
    """Load the JGB yield curve (this month; the full history on first run or with
    --history) and Japan's weekly portfolio flows from the Ministry of Finance."""
    from fin_intel.providers import MofProvider

    mof = MofProvider(raw_store=default_store())
    run_items("sync-japan", ["mof"], lambda s, _: japan.sync_japan(s, mof, history))


@app.command("sync-cboe", rich_help_panel="Macro and markets")
def sync_cboe() -> None:
    """Load Cboe options volume and put/call ratios: the archive since 2006 on first run,
    then each trading day's statistics."""
    from fin_intel.providers import CboeProvider

    cboe = CboeProvider(raw_store=default_store())
    run_items("sync-cboe", ["cboe"], lambda s, _: putcall.sync_cboe(s, cboe, date.today()))


@app.command("sync-bea", rich_help_panel="Macro and markets")
def sync_bea(
    force: Annotated[bool, typer.Option(help="Download even if synced this month")] = False,
) -> None:
    """Load BEA's input-output tables (which industries buy from which: detail 2007, 2012,
    2017; summary since 1997), then rebuild the supply-chain graph."""
    from fin_intel import industries
    from fin_intel.providers import BeaProvider

    bea = BeaProvider(raw_store=default_store())
    run_items("sync-bea", ["bea"], lambda s, _: industries.sync_bea(s, bea, force=force))


@app.command("sync-contracts", rich_help_panel="Macro and markets")
def sync_contracts(
    limit: Annotated[int | None, typer.Option(help="Most months this run")] = None,
) -> None:
    """Load federal contract obligations by industry (NAICS) from USAspending, monthly
    since October 2007; recent months are refetched until they settle."""
    from fin_intel.providers import UsaspendingProvider

    provider = UsaspendingProvider(raw_store=default_store())
    with session_factory()() as session:
        months = contracts.contract_months_due(session, date.today())[:limit]
    if months:
        run_items(
            "sync-contracts",
            [m.isoformat() for m in months],
            lambda s, m: contracts.sync_contracts_month(s, provider, date.fromisoformat(m)),
        )
