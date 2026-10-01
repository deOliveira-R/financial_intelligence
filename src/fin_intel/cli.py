import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import typer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import derive, ingest
from fin_intel.db import init_db, session_factory
from fin_intel.ingest import SNAPSHOT_DATASETS
from fin_intel.models import DailyBar, SyncRun
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
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    init_db()


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
                    typer.echo(f"{job} {item}: {fn(session, item)} rows")
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
def sync_prices(
    tickers: list[str],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: incremental")] = None,
) -> None:
    """Load daily prices, splits and dividends from Tiingo."""
    tiingo = TiingoProvider(raw_store=default_store())
    start_date = date.fromisoformat(start) if start else None
    _run("sync-prices", tickers, lambda s, t: ingest.sync_prices(s, tiingo, t, start_date))


def _weekdays(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5]


@app.command()
def sync_market_daily(
    since: Annotated[
        str | None, typer.Option(help="YYYY-MM-DD; default: day after the last stored day")
    ] = None,
    until: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: yesterday")] = None,
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
        lambda s, d: ingest.sync_market_daily(s, massive, date.fromisoformat(d)),
    )


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
def sync_economic(series: list[str]) -> None:
    """Load FRED series, e.g. GDP CPIAUCSL DGS10 FEDFUNDS UNRATE."""
    fred = FredProvider(raw_store=default_store())
    _run(
        "sync-economic", [s.upper() for s in series], lambda s, i: ingest.sync_economic(s, fred, i)
    )


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
