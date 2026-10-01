import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Annotated, Any

import typer
from sqlalchemy.orm import Session

from fin_intel import derive, ingest
from fin_intel.db import init_db, session_factory
from fin_intel.models import SyncRun
from fin_intel.providers import (
    FredProvider,
    ProviderError,
    QuotaExceededError,
    SecProvider,
    TiingoProvider,
)
from fin_intel.raw import default_store
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
