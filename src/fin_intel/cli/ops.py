"""Operations: the feed catalog, rebuilds, raw retention, migrations, the API (the schedule
is in schedule.py)."""

from datetime import timedelta
from typing import Annotated

import typer

from fin_intel import (
    registry,
)
from fin_intel.cli.app import app
from fin_intel.db import session_factory
from fin_intel.raw import default_store, prune
from fin_intel.rebuild import TARGETS, rebuild


@app.command("catalog", rich_help_panel="Operations")
def catalog_cmd(
    check: Annotated[
        bool, typer.Option(help="Exit non-zero if any feed is stale or failing")
    ] = False,
    all_feeds: Annotated[
        bool, typer.Option("--all/--alerts", help="Every feed, or only stale and failing ones")
    ] = True,
) -> None:
    """Every feed: items, failing items, last success against its expected freshness,
    raw responses, and the tables it loads. Alerts (stale, failing) first."""
    from fin_intel import catalog

    with session_factory()() as session:
        feeds = catalog.feeds(session)
    order = {"failing": 0, "stale": 1, "ok": 2, "unmonitored": 3}
    shown = sorted(feeds, key=lambda f: (order[f.status], f.provider, f.dataset))
    if not all_feeds:
        shown = [f for f in shown if f.status in ("failing", "stale")]
    for f in shown:
        age = f"{f.age_days:5.1f}d" if f.age_days is not None else "never "
        expected = f"/{f.expected_days}d" if f.expected_days else "    "
        color = {"failing": "red", "stale": "yellow"}.get(f.status)
        line = (
            f"{f.status:<11} {f.provider + '/' + f.dataset:<34} {age}{expected:<5} "
            f"{f.items:>6} items {f.failing:>4} failing  {f.terms or '?'}"
        )
        typer.secho(line, fg=color)
        if f.status == "failing" and f.last_error:
            typer.echo(f"            {f.last_error[:120]}")
    alerts = [f for f in feeds if f.status in ("failing", "stale")]
    if not alerts:
        typer.echo("all monitored feeds fresh")
    if check and alerts:
        raise typer.Exit(1)


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


@app.command("mcp", rich_help_panel="Operations")
def mcp_cmd() -> None:
    """Serve the research tools to agents over MCP (stdio): dossiers, timeline, supply
    chain, filing search and text, time series, the data catalog."""
    from fin_intel.mcp_server import server

    server.run("stdio")
