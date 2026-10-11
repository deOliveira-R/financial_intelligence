"""Portfolio: accounts, broker imports, positions and tax lots, harvesting."""

from datetime import date, timedelta
from typing import Annotated

import typer
from sqlalchemy import select

from fin_intel.cli.app import app
from fin_intel.cli.market import sync_prices
from fin_intel.db import session_factory

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
