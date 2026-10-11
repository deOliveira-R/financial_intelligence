"""Operations: the daily and weekly schedules, rebuilds, raw retention, migrations, the API."""

from datetime import date, timedelta
from typing import Annotated

import typer

from fin_intel import (
    registry,
)
from fin_intel.cli.analytics import derive_breadth_cmd, derive_metrics_cmd, derive_outcomes_cmd
from fin_intel.cli.app import app, run_steps
from fin_intel.cli.filings import (
    fill_xbrl_gaps,
    sync_crosslist,
    sync_dart,
    sync_edinet,
    sync_esef,
    sync_events,
    sync_fundamentals_bulk,
    sync_funds,
    sync_sic,
    sync_tw_prices,
    sync_twse,
)
from fin_intel.cli.macro import (
    sync_bea,
    sync_calendar,
    sync_cboe,
    sync_contracts,
    sync_cot,
    sync_economic,
    sync_eia,
    sync_japan,
)
from fin_intel.cli.market import (
    sync_actions,
    sync_adr_shares,
    sync_market_daily,
    sync_prices,
    sync_reference,
    sync_tickers,
)
from fin_intel.cli.players import (
    sync_13f,
    sync_bills,
    sync_congress,
    sync_insiders,
    sync_legislators,
    sync_lobbying,
    sync_presidential,
    sync_short_interest,
)
from fin_intel.config import get_settings
from fin_intel.db import session_factory
from fin_intel.raw import default_store, prune
from fin_intel.rebuild import TARGETS, rebuild


@app.command(rich_help_panel="Operations")
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
    run_steps("sync-daily", steps)


@app.command(rich_help_panel="Operations")
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
        ("input-output tables (BEA, monthly check)", sync_bea),
        ("short interest (FINRA)", sync_short_interest),
        ("index ETF holdings (N-PORT)", sync_funds),
        ("members of Congress and committees", sync_legislators),
        ("lobbying reports (Senate LDA)", sync_lobbying),
        ("bills in Congress (GovInfo)", sync_bills),
        ("presidential documents (Federal Register)", sync_presidential),
        ("forward outcomes", derive_outcomes_cmd),
    ]
    steps.append(("raw retention", lambda: prune_raw(keep=3, min_age_days=31, dry_run=False)))
    run_steps("sync-weekly", steps)


@app.command("rebuild", rich_help_panel="Operations")
def rebuild_cmd(
    target: Annotated[str, typer.Argument(help=f"One of: {', '.join(TARGETS)}")],
) -> None:
    """Rebuild tables from stored raw responses (no network). Use after parser changes."""
    if target not in TARGETS:
        raise typer.BadParameter(f"choose from {', '.join(TARGETS)}")
    with session_factory()() as session:
        for dataset, count in sorted(rebuild(session, default_store(), target).items()):
            typer.echo(f"replayed {count:>5} {dataset}")


@app.command(rich_help_panel="Operations")
def prune_raw(
    keep: Annotated[int, typer.Option(help="Snapshot responses to keep per item")] = 3,
    min_age_days: Annotated[int, typer.Option(help="Never prune anything younger")] = 31,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Drop raw responses no rebuild can need (old snapshots, old errors)."""
    from datetime import timedelta

    result = prune(
        default_store(),
        registry.where(snapshot=True),
        keep=keep,
        min_age=timedelta(days=min_age_days),
        dry_run=dry_run,
    )
    verb = "would remove" if dry_run else "removed"
    typer.echo(
        f"{verb} {result.responses} responses, {result.files} files "
        f"({result.bytes / 1_048_576:.1f} MB)"
    )


@app.command(rich_help_panel="Operations")
def migrate() -> None:
    """Apply pending database migrations (every command does this first; deploys call it
    explicitly before restarting the API)."""
    typer.echo("database schema is up to date")


@app.command(rich_help_panel="Operations")
def make_migration(message: str) -> None:
    """Generate a migration from changes to models.py (review it before committing)."""
    from alembic import command

    from fin_intel.db import MIGRATIONS, alembic_config, get_engine

    rev_id = f"{len(list((MIGRATIONS / 'versions').glob('[0-9]*.py'))) + 1:04d}"
    with get_engine().begin() as connection:
        command.revision(
            alembic_config(connection), message=message, autogenerate=True, rev_id=rev_id
        )


@app.command(rich_help_panel="Operations")
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the HTTP API."""
    import uvicorn

    uvicorn.run("fin_intel.api:app", host=host, port=port, reload=reload)
