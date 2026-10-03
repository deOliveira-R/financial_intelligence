import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any

import typer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import derive, ingest
from fin_intel.config import get_settings
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
def sync_fundamentals_bulk(
    zip_path: Annotated[
        str | None, typer.Option("--zip", help="Use an already downloaded companyfacts.zip")
    ] = None,
    all_filers: Annotated[
        bool, typer.Option(help="Every SEC filer, not just issuers with a security")
    ] = False,
) -> None:
    """Load SEC fundamentals for every tracked issuer from the nightly bulk file.

    The first run loads everything (hours); later runs reload only companies whose data
    changed. Each company becomes its own raw response, as with sync-fundamentals.
    """
    from pathlib import Path

    from fin_intel.config import get_settings as settings

    store = default_store()
    downloaded = zip_path is None
    path = (
        Path(zip_path) if zip_path else Path(settings().raw_dir).parent / "tmp" / "companyfacts.zip"
    )

    def work(session: Session, _: str) -> int:
        if downloaded:
            typer.echo("downloading companyfacts.zip ...")
            SecProvider(raw_store=store).download_bulk_company_facts(path)
        ciks = None if all_filers else ingest.tracked_ciks(session)

        def progress(n: int, total: int) -> None:
            if n % 250 == 0 or n == total:
                typer.echo(f"  {n}/{total} companies")

        loaded, unchanged = ingest.load_bulk_company_facts(session, store, path, ciks, progress)
        typer.echo(f"loaded {loaded}, unchanged {unchanged}")
        return loaded

    try:
        _run("sync-fundamentals-bulk", ["bulk"], work)
    finally:
        if downloaded:
            path.unlink(missing_ok=True)


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
    days = _weekdays(start, end)
    if not days:
        typer.echo("up to date")
        return
    _run(
        "sync-market-daily",
        [d.isoformat() for d in days],
        lambda s, d: ingest.sync_market_daily(s, massive, date.fromisoformat(d), otc),
    )


@app.command()
def sync_reference(
    otc: Annotated[
        bool, typer.Option("--otc", help="Also load OTC tickers (~20 more calls)")
    ] = False,
) -> None:
    """Load Massive's reference tickers: types and FIGIs for all, plus ETFs and funds SEC
    doesn't list. Run after sync-tickers; weekly is plenty."""
    massive = MassiveProvider(raw_store=default_store())
    markets = ["stocks", "otc"] if otc else ["stocks"]
    _run("sync-reference", markets, lambda s, m: ingest.sync_reference_tickers(s, massive, m))


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
def sync_economic(
    series: Annotated[
        list[str] | None, typer.Argument(help="FRED ids; default: macro pack")
    ] = None,
) -> None:
    """Load FRED series with their revision history. Default: the curated macro pack
    (macro.py) or FI_FRED_SERIES."""
    fred = FredProvider(raw_store=default_store())
    ids = [s.upper() for s in series] if series else get_settings().fred_series_ids
    _run("sync-economic", ids, lambda s, i: ingest.sync_economic(s, fred, i))


def _steps(job: str, steps: list[tuple[str, Callable[[], None]]]) -> None:
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


@app.command()
def sync_daily() -> None:
    """Scheduled daily sync: market bars, recent actions, watchlist prices, FRED series.

    Run after the US close (data is end-of-day). Lists come from FI_WATCHLIST and
    FI_FRED_SERIES; OTC bars follow FI_MARKET_OTC.
    """
    settings = get_settings()
    recent = (date.today() - timedelta(days=30)).isoformat()  # catches late corrections
    steps = [
        ("market bars", lambda: sync_market_daily(otc=settings.market_otc)),
        ("splits and dividends", lambda: sync_actions(since=recent)),
        ("market breadth", derive_breadth_cmd),
        ("economic series", lambda: sync_economic(settings.fred_series_ids)),
    ]
    if settings.watchlist_tickers:
        steps.append(("watchlist prices", lambda: sync_prices(settings.watchlist_tickers)))
    _steps("sync-daily", steps)


@app.command()
def sync_weekly() -> None:
    """Scheduled weekly sync: security lists, watchlist fundamentals, raw retention."""
    settings = get_settings()
    steps = [
        ("SEC tickers", sync_tickers),
        ("Massive reference", lambda: sync_reference(otc=settings.market_otc)),
    ]
    if settings.watchlist_tickers:
        steps.append(("fundamentals", lambda: sync_fundamentals(settings.watchlist_tickers)))
    steps.append(("raw retention", lambda: prune_raw(keep=3, min_age_days=31, dry_run=False)))
    _steps("sync-weekly", steps)


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


@app.command("derive-breadth")
def derive_breadth_cmd() -> None:
    """Recompute market breadth from stored bars (no network)."""
    from fin_intel import breadth

    with session_factory()() as session:
        typer.echo(f"breadth: {breadth.compute(session)} days")


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
def migrate() -> None:
    """Apply pending database migrations (every command does this first; deploys call it
    explicitly before restarting the API)."""
    typer.echo("database schema is up to date")


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


# --- research -------------------------------------------------------------------------


@app.command("timeseries")
def timeseries_cmd(
    specs: Annotated[list[str], typer.Argument(help="e.g. 'px:SPY|sma:200' 'fred:T10Y2Y'")],
    start: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    end: Annotated[str | None, typer.Option(help="YYYY-MM-DD")] = None,
    pit: Annotated[bool, typer.Option(help="FRED values as known on each date")] = True,
    csv_path: Annotated[str | None, typer.Option("--csv", help="Write all rows to a CSV")] = None,
    rows: Annotated[int, typer.Option(help="Rows to print")] = 10,
) -> None:
    """Prices, macro and indicators on the trading calendar (see timeseries.py for syntax)."""
    import csv

    from fin_intel import timeseries

    with session_factory()() as session:
        try:
            dates, series = timeseries.build(
                session,
                specs,
                date.fromisoformat(start) if start else None,
                date.fromisoformat(end) if end else None,
                pit,
            )
        except timeseries.SpecError as exc:
            raise typer.BadParameter(str(exc)) from None
    if csv_path:
        with open(csv_path, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["date", *series])
            for i, d in enumerate(dates):
                writer.writerow([d.isoformat(), *(v[i] for v in series.values())])
        typer.echo(f"wrote {len(dates)} rows to {csv_path}")
    width = max(12, *(len(k) for k in series))
    typer.echo(f"{'date':<11}" + "".join(f"{k:>{width + 2}}" for k in series))
    for i in range(max(0, len(dates) - rows), len(dates)):
        cells = "".join(
            f"{'—' if v[i] is None else f'{v[i]:.4g}':>{width + 2}}" for v in series.values()
        )
        typer.echo(f"{dates[i].isoformat():<11}{cells}")


# --- portfolio -------------------------------------------------------------------------

portfolio_app = typer.Typer(
    help="Your accounts, tax lots and tax-loss harvesting", no_args_is_help=True
)
app.add_typer(portfolio_app, name="portfolio")

ACCOUNT_TYPES = {"taxable": True, "ira": False, "roth_ira": False, "401k": False, "hsa": False}


def _money(value: float | None) -> str:
    return "—" if value is None else f"{value:,.2f}"


@portfolio_app.command("add-account")
def portfolio_add_account(
    name: str,
    broker: Annotated[str, typer.Option(help="fidelity, vanguard, manual, ...")],
    account_type: Annotated[str, typer.Option("--type", help=", ".join(ACCOUNT_TYPES))],
    last4: Annotated[str | None, typer.Option(help="Last 4 digits of the account number")] = None,
) -> None:
    """Register an account. Only taxable accounts are harvesting candidates."""
    from fin_intel.models import Account

    if account_type not in ACCOUNT_TYPES:
        raise typer.BadParameter(f"--type must be one of {', '.join(ACCOUNT_TYPES)}")
    with session_factory()() as session:
        session.add(
            Account(
                name=name,
                broker=broker,
                account_type=account_type,
                taxable=ACCOUNT_TYPES[account_type],
                number_last4=last4,
            )
        )
        session.commit()
    typer.echo(f"added {name} ({broker}, {account_type})")


@portfolio_app.command("accounts")
def portfolio_accounts() -> None:
    from fin_intel.models import Account

    with session_factory()() as session:
        for a in session.scalars(select(Account).order_by(Account.name)):
            tax = "taxable" if a.taxable else "tax-advantaged"
            typer.echo(
                f"{a.name:<28} {a.broker:<10} {a.account_type:<9} {tax:<15} {a.number_last4 or ''}"
            )


@portfolio_app.command("import")
def portfolio_import(
    path: str,
    fmt: Annotated[
        str, typer.Option("--format", help="generic (Fidelity/Vanguard to come)")
    ] = "generic",
) -> None:
    """Import a transactions export. Re-importing overlapping exports is safe."""
    from fin_intel import importers

    reader = importers.TRANSACTION_FORMATS.get(fmt)
    if reader is None:
        raise typer.BadParameter(
            f"--format must be one of {', '.join(importers.TRANSACTION_FORMATS)}"
        )
    with session_factory()() as session:
        try:
            new = importers.load_transactions(session, reader(path), source=fmt)
        except importers.PortfolioImportError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from None
    typer.echo(f"{new} new transactions")


@portfolio_app.command("import-positions")
def portfolio_import_positions(
    path: str,
    fmt: Annotated[str, typer.Option("--format", help="generic or fidelity")] = "generic",
) -> None:
    """Import a positions snapshot. Accounts without transaction history are reported from
    it; otherwise it reconciles derived lots and prices funds without market data."""
    from fin_intel import importers

    if fmt not in importers.POSITION_FORMATS:
        raise typer.BadParameter(f"--format must be one of {', '.join(importers.POSITION_FORMATS)}")
    with session_factory()() as session:
        try:
            if fmt == "fidelity":
                rows, accounts = importers.read_fidelity_positions(path)
                for name in importers.ensure_accounts(session, accounts, "fidelity"):
                    account_type, last4 = accounts[name]
                    typer.echo(f"created account {name} ({account_type}, ...{last4})")
            else:
                rows = importers.POSITION_FORMATS[fmt](path)
            count = importers.load_positions(session, rows)
        except importers.PortfolioImportError as exc:
            typer.secho(str(exc), fg="red", err=True)
            raise typer.Exit(1) from None
    typer.echo(f"{count} positions")


@portfolio_app.command("positions")
def portfolio_positions() -> None:
    """Holdings with cost, value and unrealized gain (short/long term)."""
    from fin_intel import portfolio

    with session_factory()() as session:
        rows = portfolio.positions(session)
        warnings = portfolio.lot_book(session).warnings
    typer.echo(
        f"{'account':<26} {'symbol':<8} {'shares':>12} {'cost':>14} {'value':>14} "
        f"{'unrealized':>13} {'short':>12} {'long':>12}  check"
    )
    for p in rows:
        if p.basis_source == "broker":
            check = "broker basis"
        elif p.reconciled is None:
            check = ""
        else:
            check = "ok" if p.reconciled else f"broker: {p.broker_quantity:g}"
        typer.echo(
            f"{p.account:<26} {p.symbol:<8} {p.quantity:>12,.4f} {_money(p.cost_basis):>14} "
            f"{_money(p.market_value):>14} {_money(p.unrealized):>13} "
            f"{_money(p.unrealized_short):>12} {_money(p.unrealized_long):>12}  {check}"
        )
    for w in warnings:
        typer.secho(f"warning: {w}", fg="yellow", err=True)


@portfolio_app.command("harvest")
def portfolio_harvest(
    min_loss: Annotated[float, typer.Option(help="Minimum loss in dollars")] = 0.0,
    min_loss_pct: Annotated[float, typer.Option(help="Minimum loss as a fraction, e.g. 0.1")] = 0.0,
) -> None:
    """Taxable lots with unrealized losses, and what would wash the loss."""
    from fin_intel import portfolio

    with session_factory()() as session:
        candidates = portfolio.harvest_candidates(session, min_loss, min_loss_pct)
    if not candidates:
        typer.echo("no harvesting candidates")
    for c in candidates:
        lot = c.lot
        held = (
            f"acquired {lot.acquired} ({lot.term}-term)"
            if lot.acquired
            else "holding period unknown"
        )
        typer.echo(
            f"{lot.account:<26} {lot.symbol:<8} {lot.quantity:>10,.4f} sh {held}: "
            f"loss {_money(c.loss)} ({c.loss_pct:.1%})"
        )
        if not c.history_known:
            typer.secho(
                "    no transaction history: can't check purchases in the last 30 days "
                "(including reinvested dividends)",
                fg="yellow",
            )
        for account, day, shares in c.blocking_purchases:
            typer.secho(f"    would wash: bought {shares:g} on {day} in {account}", fg="yellow")
        typer.echo(f"    don't buy it back before {c.rebuy_after}")


@portfolio_app.command("realized")
def portfolio_realized(year: Annotated[int | None, typer.Option(help="Tax year")] = None) -> None:
    """Realized gains and losses (FIFO), split short/long term."""
    from fin_intel import portfolio

    with session_factory()() as session:
        realized = [
            r for r in portfolio.lot_book(session).realized if year is None or r.sold.year == year
        ]
    totals: dict[str, float] = {"short": 0.0, "long": 0.0}
    for r in realized:
        flag = "  possible wash sale" if r.wash_sale_risk else ""
        typer.echo(
            f"{r.sold} {r.symbol:<8} {r.quantity:>10,.4f} sh  acquired {r.acquired or '?'}  "
            f"{r.term or '?':<5} gain {_money(r.gain)}{flag}"
        )
        if r.gain is not None and r.term:
            totals[r.term] += r.gain
    typer.echo(f"short-term {_money(totals['short'])}   long-term {_money(totals['long'])}")


@portfolio_app.command("prices")
def portfolio_prices() -> None:
    """Fetch Tiingo prices for holdings without recent market data (e.g. mutual funds)."""
    from fin_intel import portfolio

    with session_factory()() as session:
        held = {lot.symbol for lot in portfolio.lot_book(session).open}
        priced = portfolio.latest_prices(session, held)
    stale = date.today() - timedelta(days=7)
    missing = sorted(
        s for s in held if s not in priced or priced[s][2] == "broker" or priced[s][1] < stale
    )
    if not missing:
        typer.echo("all holdings have recent prices")
        return
    sync_prices(missing)
