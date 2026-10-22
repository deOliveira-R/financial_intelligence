"""The schedule as steps with states (jobs.py): what each step runs, what it reads, and the
commands that mark steps due, run them until they succeed, and show where they stand."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
from collections import deque
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer
from sqlalchemy import select, text

from fin_intel import jobs
from fin_intel.cli.analytics import (
    derive_breadth_cmd,
    derive_metrics_cmd,
    derive_outcomes_cmd,
    derive_quarterly_prices_cmd,
)
from fin_intel.cli.app import app
from fin_intel.cli.filings import (
    fill_xbrl_gaps,
    sync_crosslist,
    sync_dart,
    sync_edinet,
    sync_esef,
    sync_events,
    sync_filing_text,
    sync_fundamentals_bulk,
    sync_funds,
    sync_population,
    sync_sic,
    sync_tw_prices,
    sync_twse,
)
from fin_intel.cli.macro import (
    sync_bis,
    sync_calendar,
    sync_cboe,
    sync_commodities,
    sync_contracts,
    sync_cot,
    sync_economic,
    sync_eia,
    sync_industries,
    sync_japan,
    sync_panel,
    sync_volatility,
)
from fin_intel.cli.market import (
    sync_actions,
    sync_adr_shares,
    sync_market_daily,
    sync_prices,
    sync_reference,
    sync_tickers,
)
from fin_intel.cli.ops import catalog_cmd, prune_raw
from fin_intel.cli.players import (
    sync_13f,
    sync_bills,
    sync_conflict,
    sync_congress,
    sync_insiders,
    sync_legislators,
    sync_lobbying,
    sync_news,
    sync_presidential,
    sync_short_interest,
)
from fin_intel.cli.research import distress_cmd
from fin_intel.config import get_settings
from fin_intel.db import get_engine, session_factory
from fin_intel.jobs import Step
from fin_intel.models import JobEvent, JobState
from fin_intel.providers import NotConfiguredError

LOCK_ID = 7_301_993  # pg advisory lock: one reconciler at a time
BACKUP_SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "backup.sh"


def _backup() -> None:
    if not os.environ.get("FI_BACKUP_REMOTE"):
        raise NotConfiguredError("FI_BACKUP_REMOTE is not set (an rclone remote, e.g. oci:bucket)")
    script = BACKUP_SCRIPT if BACKUP_SCRIPT.exists() else Path("/app/deploy/backup.sh")
    if subprocess.run([str(script)]).returncode:
        raise typer.Exit(1)


def steps() -> list[Step]:
    """Every scheduled step, in the order they run. `after`: the steps whose data it reads
    (it waits for them while they're in flight and runs again when they succeed late)."""
    settings = get_settings()
    recent = (date.today() - timedelta(days=30)).isoformat()  # catches late corrections
    daily_inputs = ("market-bars", "actions", "fundamentals", "economic", "dart", "edinet")
    out = [
        # daily, after the US close
        Step(
            "market-bars",
            "market bars",
            "daily",
            lambda: sync_market_daily(otc=settings.market_otc),
        ),
        Step("actions", "splits and dividends", "daily", lambda: sync_actions(since=recent)),
        Step("breadth", "market breadth", "daily", derive_breadth_cmd, after=("market-bars",)),
        Step(
            "fundamentals",
            "fundamentals (SEC bulk, changed companies only)",
            "daily",
            sync_fundamentals_bulk,
            timeout=timedelta(hours=6),
        ),
        Step("insiders", "insider transactions", "daily", sync_insiders),
        Step("congress", "congressional trades", "daily", sync_congress),
        Step(
            "economic",
            "economic series (FRED)",
            "daily",
            lambda: sync_economic(settings.fred_series_ids),
        ),
        Step("eia", "EIA energy data", "daily", sync_eia),
        Step("japan", "Japanese rates and flows (MoF)", "daily", sync_japan),
        Step("cboe", "options put/call (Cboe)", "daily", sync_cboe),
        Step("volatility", "volatility and strategy indices (Cboe)", "daily", sync_volatility),
        Step("dart", "Korean filings (DART)", "daily", lambda: sync_dart(limit=6000)),
        Step("edinet", "Japanese filings (EDINET)", "daily", sync_edinet),
        Step("tw-prices", "Taiwanese prices", "daily", sync_tw_prices),
        Step(
            "metrics",
            "company metrics",
            "daily",
            derive_metrics_cmd,
            after=(*daily_inputs, "tw-prices"),
        ),
        Step(
            "distress",
            "distress alerts on watched companies (H001)",
            "daily",
            lambda: distress_cmd(score=True),
            after=("fundamentals", "market-bars"),
        ),
    ]
    if settings.watchlist_tickers:
        out.append(
            Step(
                "watchlist-prices",
                "watchlist prices",
                "daily",
                lambda: sync_prices(settings.watchlist_tickers),
            )
        )
    weekly = [
        ("tickers", "SEC tickers", sync_tickers),
        ("reference", "Massive reference", lambda: sync_reference(otc=settings.market_otc)),
        ("13f", "institutional holdings (13F)", lambda: sync_13f(files=4)),
        ("cftc", "CFTC positioning", sync_cot),
        ("adr-shares", "ADR share counts", sync_adr_shares),
        ("sic", "SIC codes of new issuers", sync_sic),
        ("events", "corporate events (SEC filings)", sync_events),
        (
            "population",
            "research population (every SEC financial filer since 2009)",
            sync_population,
        ),
        ("xbrl-gaps", "filings missing from SEC company facts", fill_xbrl_gaps),
        ("filing-text", "filing text of the deep-history universe (new filings)", sync_filing_text),
        ("twse", "Taiwanese companies (TWSE/TPEx)", sync_twse),
        ("esef", "European annual reports (ESEF)", sync_esef),
        ("crosslist", "cross-listings of foreign companies", sync_crosslist),
        ("calendar", "economic release calendar", sync_calendar),
        ("jgb-history", "JGB history (MoF, monthly file)", lambda: sync_japan(history=True)),
        ("contracts", "federal contract obligations (USAspending)", sync_contracts),
        ("industries", "input-output tables and concordances (BEA, Census)", sync_industries),
        ("bis", "credit by sector, ~45 economies (BIS)", sync_bis),
        ("commodities", "commodity prices since 1960 (World Bank Pink Sheet)", sync_commodities),
        ("panel", "cross-country research panel (OECD, Macrohistory)", sync_panel),
        ("short-interest", "short interest (FINRA)", sync_short_interest),
        ("funds", "index ETF holdings (N-PORT)", sync_funds),
        ("legislators", "members of Congress and committees", sync_legislators),
        ("lobbying", "lobbying reports (Senate LDA)", sync_lobbying),
        ("bills", "bills in Congress (GovInfo)", sync_bills),
        ("presidential", "presidential documents (Federal Register)", sync_presidential),
        ("conflict", "conflict events (UCDP; ACLED with a Research account)", sync_conflict),
        ("outcomes", "forward outcomes", derive_outcomes_cmd),
        (
            "raw-retention",
            "raw retention",
            lambda: prune_raw(keep=3, min_age_days=31, dry_run=False),
        ),
    ]
    out += [
        Step(name, label, "weekly", fn, timeout=timedelta(hours=6)) for name, label, fn in weekly
    ]
    out.append(
        Step(
            "quarterly-prices",
            "quarter-end prices from 13F values",
            "weekly",
            derive_quarterly_prices_cmd,
            after=("13f",),
            timeout=timedelta(hours=2),
        )
    )
    out.append(
        Step(
            "news",
            "news attention, the stalest query (GDELT)",
            "news",
            lambda: sync_news(stalest=1),
        )
    )
    out.append(
        Step("backup", "backup to the rclone remote", "backup", _backup, timeout=timedelta(hours=3))
    )
    return out


def run_step(step: Step) -> jobs.Outcome:
    """Run one step here and say how it ended (the child process's side of `execute`)."""
    jobs.take_errors()
    try:
        step.run()
        code = 0
    except typer.Exit as exc:
        code = exc.exit_code
    except Exception as exc:  # noqa: BLE001  (classified and recorded, not swallowed)
        jobs.record_error(exc)
        traceback.print_exc()
        code = 1
    errors = jobs.take_errors()
    if code == 0:
        return jobs.Outcome(ok=True)
    return jobs.classify(errors)


def execute(step: Step) -> jobs.Outcome:
    """Run a step in its own process: a crash or a leak can't take the reconciler down, a
    hung step is stopped at its time limit, and its output is kept (and passed through)."""
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "outcome.json"
        cmd = [
            sys.executable,
            "-c",
            "from fin_intel.cli import app; app()",
            "jobs",
            "exec",
            step.name,
            "--report",
            str(report),
        ]
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        tail: deque[str] = deque(maxlen=400)

        def pump() -> None:
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line)
                tail.append(line)

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        try:
            proc.wait(timeout=step.timeout.total_seconds())
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            reader.join(timeout=10)
            limit = step.timeout.total_seconds() / 3600
            return jobs.Outcome(
                ok=False, kind="timeout", error=f"stopped after {limit:g} h", output="".join(tail)
            )
        reader.join(timeout=10)
        output = "".join(tail)
        if not report.exists():
            # Killed (out of memory: -9) or failed before it could report (an import error).
            kind = "interrupted" if proc.returncode < 0 else "crash"
            return jobs.Outcome(
                ok=False,
                kind=kind,
                error=f"exited {proc.returncode} without a report",
                output=output,
            )
        data = json.loads(report.read_text())
        return jobs.Outcome(ok=data["ok"], kind=data["kind"], error=data["error"], output=output)


def _by_name(name: str) -> Step:
    found = {s.name: s for s in steps()}
    if name not in found:
        raise typer.BadParameter(f"unknown step {name!r}; one of {', '.join(found)}")
    return found[name]


jobs_app = typer.Typer(help="Scheduled steps: states, retries until success", no_args_is_help=True)
app.add_typer(jobs_app, name="jobs", rich_help_panel="Operations")


@jobs_app.command("exec", hidden=True)
def exec_cmd(name: str, report: Annotated[Path, typer.Option()]) -> None:
    """Run one step in this process and write its outcome (used by `jobs run`)."""
    outcome = run_step(_by_name(name))
    report.write_text(json.dumps({"ok": outcome.ok, "kind": outcome.kind, "error": outcome.error}))


def reconcile(executor=None) -> jobs.Summary | None:
    """Run every runnable step, holding the reconciler lock; None if another holds it."""
    with get_engine().connect() as lock:
        if not lock.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": LOCK_ID}).scalar():
            return None
        lock.commit()  # the lock is the session's; don't sit idle in a transaction for hours
        try:
            with session_factory()() as session:
                return jobs.reconcile(session, steps(), executor or execute)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": LOCK_ID})
            lock.commit()


def _report(summary: jobs.Summary | None) -> None:
    if summary is None:
        typer.echo("another reconciler is running; it picks up whatever is due")
        return
    for name in summary.interrupted:
        typer.secho(f"{name}: had been interrupted; retrying", fg="yellow")
    for name, status in summary.ran:
        color = None if status == jobs.OK else "red" if status == jobs.FAILED else "yellow"
        typer.secho(f"{name}: {status}", fg=color)


@jobs_app.command("run")
def run_cmd() -> None:
    """Run every step that is due or whose retry time has come, until none is left (the
    schedule calls this every few minutes)."""
    _report(reconcile())


@jobs_app.command("due")
def due_cmd(group: Annotated[str, typer.Argument(help="daily, weekly, news or backup")]) -> None:
    """Mark a group's steps due (the schedule's part); `jobs run` runs them."""
    groups = {s.group for s in steps()}
    if group not in groups:
        raise typer.BadParameter(f"one of {', '.join(sorted(groups))}")
    with session_factory()() as session:
        count = jobs.mark_due(session, steps(), group, datetime.now(UTC))
    typer.echo(f"{count} {group} steps due")


@jobs_app.command("retry")
def retry_cmd(names: list[str]) -> None:
    """Retry these steps at the next `jobs run` instead of waiting for their backoff."""
    with session_factory()() as session:
        for name in names:
            jobs.retry_now(session, _by_name(name).name, datetime.now(UTC))
    typer.echo(f"due now: {' '.join(names)}")


def _age(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return "never"
    hours = (now - moment).total_seconds() / 3600
    return f"{hours:.0f}h ago" if hours < 48 else f"{hours / 24:.0f}d ago"


@jobs_app.command("status")
def status_cmd(
    all_steps: Annotated[bool, typer.Option("--all/--problems")] = False,
    events: Annotated[int, typer.Option(help="Recent attempts of each problem step")] = 0,
) -> None:
    """Every step's state; by default only those not ok, with the error and what to do."""
    now = datetime.now(UTC)
    with session_factory()() as session:
        states = {s.name: s for s in session.scalars(select(JobState))}
        shown = 0
        for step in steps():
            state = states.get(step.name)
            status = state.status if state else "never run"
            if not all_steps and status in (jobs.OK, "never run"):
                continue
            shown += 1
            color = {jobs.FAILED: "red", jobs.BLOCKED: "red", jobs.RETRYING: "yellow"}.get(status)
            success = _age(state.last_success if state else None, now)
            line = f"{status:<10} {step.name:<16} last ok {success:<9}"
            if state and state.attempts:
                line += f" {state.attempts} failed"
            if state and state.next_attempt and status != jobs.OK:
                wait = (state.next_attempt - now).total_seconds() / 60
                line += f", next {'now' if wait <= 0 else f'in {wait:.0f} min'}"
            typer.secho(line, fg=color)
            if state and state.error and status != jobs.OK:
                typer.echo(f"           {state.error_kind}: {state.error[:200]}")
                typer.echo(f"           {state.hint}")
            if events and state and status != jobs.OK:
                rows = session.scalars(
                    select(JobEvent)
                    .where(JobEvent.name == step.name)
                    .order_by(JobEvent.id.desc())
                    .limit(events)
                )
                for e in rows:
                    typer.echo(f"           {e.finished_at:%Y-%m-%d %H:%M} {e.status}: {e.error}")
        if not shown:
            typer.echo("every step ok")
    catalog_cmd(check=False, all_feeds=False)


def _run_group(group: str) -> None:
    with session_factory()() as session:
        jobs.mark_due(session, steps(), group, datetime.now(UTC))
    summary = reconcile()
    _report(summary)
    if summary and any(status != jobs.OK for _, status in summary.ran):
        raise typer.Exit(1)


@app.command(rich_help_panel="Operations")
def sync_daily() -> None:
    """Mark the daily steps due and run them now (what the schedule does after the US close):
    market bars, actions, breadth, fundamentals, insiders, congress, FRED, EIA, Japan, Cboe,
    DART, EDINET, Taiwan, metrics, distress alerts, watchlist prices. Failed steps are
    retried by `jobs run` until they succeed."""
    _run_group("daily")


@app.command(rich_help_panel="Operations")
def sync_weekly() -> None:
    """Mark the weekly steps due and run them now: security lists, 13F, CFTC, events, filing
    text, foreign statements, macro panels, Congress, lobbying, outcomes, raw retention."""
    _run_group("weekly")
