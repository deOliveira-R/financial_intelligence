"""Portfolio (personal data: always behind the API key when one is configured)."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from fin_intel import (
    portfolio,
)
from fin_intel.api.app import Orm, SessionDep
from fin_intel.models import (
    Account,
)

router = APIRouter()


class AccountOut(Orm):
    id: int
    name: str
    broker: str
    account_type: str
    taxable: bool
    number_last4: str | None


@router.get("/portfolio/accounts", response_model=list[AccountOut])
def portfolio_accounts(session: SessionDep) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.name)))


@router.get("/portfolio/positions")
def portfolio_positions(session: SessionDep) -> list[portfolio.Position]:
    """Holdings per account with cost, value and unrealized gain split by holding term;
    `reconciled` compares the derived quantity with the broker's latest snapshot."""
    return portfolio.positions(session)


@router.get("/portfolio/lots")
def portfolio_lots(session: SessionDep) -> list[portfolio.LotView]:
    return portfolio.lot_views(session)


@router.get("/portfolio/realized")
def portfolio_realized(session: SessionDep, year: int | None = None) -> list[dict]:
    rows = portfolio.lot_book(session).realized
    return [{**asdict(r), "gain": r.gain} for r in rows if year is None or r.sold.year == year]


@router.get("/portfolio/harvest")
def portfolio_harvest(
    session: SessionDep, min_loss: float = 0.0, min_loss_pct: float = 0.0
) -> list[portfolio.HarvestCandidate]:
    """Taxable lots with unrealized losses; `blocking_purchases` would wash the loss."""
    return portfolio.harvest_candidates(session, min_loss, min_loss_pct)


@router.get("/portfolio/context/{ticker}")
def portfolio_context(session: SessionDep, ticker: str) -> portfolio.Context:
    """Price vs its 52-week range and 200-day average, and returns vs SPY."""
    result = portfolio.context(session, ticker)
    if result is None:
        raise HTTPException(404, f"no price history for {ticker}")
    return result


@router.get("/portfolio/replacements/{ticker}")
def portfolio_replacements(
    session: SessionDep, ticker: str, top: int = Query(10, le=50)
) -> list[portfolio.Replacement]:
    """Liquid ETFs that track `ticker` most closely (lowest tracking error): exposure to
    keep while a harvested loss waits out the wash-sale window. One tracking the same
    index may count as substantially identical."""
    return portfolio.replacements(session, ticker, top)
