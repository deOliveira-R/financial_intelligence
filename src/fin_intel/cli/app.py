"""The CLI application and what every command shares: per-item runs recorded in
sync_runs, and scheduled steps that keep going when one fails."""

import logging
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import typer
from sqlalchemy.orm import Session

from fin_intel.db import init_db, session_factory
from fin_intel.models import SyncRun
from fin_intel.providers import (
    ProviderError,
    QuotaExceededError,
)

app = typer.Typer(help="Financial Intelligence data backend", no_args_is_help=True)


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    # Under systemd stdout is a pipe, block-buffered by default: progress would show up late.
    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING)
    init_db()


def run_items(job: str, items: list[str], fn: Callable[[Session, str], Any]) -> None:
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


def weekdays(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5]


def run_steps(job: str, steps: list[tuple[str, Callable[[], None]]]) -> None:
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
