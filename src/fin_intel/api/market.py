"""Securities, prices, corporate actions, index ETF holdings."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from fin_intel import (
    funds,
)
from fin_intel.api.app import Orm, SessionDep, security_or_404
from fin_intel.models import (
    CorporateAction,
    DailyBar,
    Security,
    TickerHistory,
)
from fin_intel.prices import adjustments

router = APIRouter()


class SecurityOut(Orm):
    id: int
    ticker: str | None
    active: bool
    origin: str
    name: str | None
    security_type: str | None
    exchange: str | None
    mic: str | None
    cik: int | None
    figi: str | None
    share_class_figi: str | None


class TickerHistoryOut(Orm):
    ticker: str
    first_seen: date
    last_seen: date


class SecurityDetailOut(SecurityOut):
    ticker_history: list[TickerHistoryOut]


class BarOut(BaseModel):
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    adj_open: float | None
    adj_high: float | None
    adj_low: float | None
    adj_close: float | None
    adj_volume: float | None


class ActionOut(Orm):
    ex_date: date
    action: str
    value: float
    source: str


@router.get("/securities", response_model=list[SecurityOut])
def list_securities(
    session: SessionDep,
    q: str | None = None,
    type: str | None = Query(None, description="e.g. CS, ETF, PFD, WARRANT, ADRC, FUND"),
    active: bool | None = None,
    limit: int = Query(50, le=500),
) -> list[Security]:
    stmt = select(Security).order_by(Security.ticker).limit(limit)
    if type:
        stmt = stmt.where(Security.security_type == type.upper())
    if active is not None:
        stmt = stmt.where(Security.active == active)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(Security.ticker.ilike(like) | Security.name.ilike(like))
    return list(session.scalars(stmt))


@router.get("/securities/{ticker}", response_model=SecurityDetailOut)
def get_security(session: SessionDep, ticker: str) -> SecurityDetailOut:
    security = security_or_404(session, ticker)
    history = session.scalars(
        select(TickerHistory)
        .where(TickerHistory.security_id == security.id)
        .order_by(TickerHistory.first_seen)
    )
    return SecurityDetailOut(
        **SecurityOut.model_validate(security).model_dump(),
        ticker_history=[TickerHistoryOut.model_validate(h) for h in history],
    )


@router.get("/prices/{ticker}/daily", response_model=list[BarOut])
def daily_prices(
    session: SessionDep,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    source: str = "tiingo",
) -> list[BarOut]:
    """Unadjusted bars plus split- and dividend-adjusted values computed from stored actions."""
    security = security_or_404(session, ticker)
    # Adjusting a bar needs every later action and the close before each, so load through
    # the latest bar and cut to `end` afterwards.
    stmt = select(DailyBar).where(DailyBar.security_id == security.id, DailyBar.source == source)
    if start:
        stmt = stmt.where(DailyBar.date >= start)
    bars = list(session.scalars(stmt.order_by(DailyBar.date)))
    actions = session.execute(
        select(CorporateAction.ex_date, CorporateAction.action, CorporateAction.value).where(
            CorporateAction.security_id == security.id, CorporateAction.source == source
        )
    ).all()
    factors = adjustments(bars, [tuple(a) for a in actions])

    def scaled(value: float | None, factor: float) -> float | None:
        return None if value is None else value * factor

    out = []
    for b in bars:
        if end and b.date > end:
            break
        f = factors[b.date]
        out.append(
            BarOut(
                date=b.date,
                open=b.open,
                high=b.high,
                low=b.low,
                close=b.close,
                volume=b.volume,
                adj_open=scaled(b.open, f.price),
                adj_high=scaled(b.high, f.price),
                adj_low=scaled(b.low, f.price),
                adj_close=scaled(b.close, f.price),
                adj_volume=scaled(b.volume, f.volume),
            )
        )
    return out


@router.get("/prices/{ticker}/actions", response_model=list[ActionOut])
def corporate_actions(session: SessionDep, ticker: str) -> list[CorporateAction]:
    security = security_or_404(session, ticker)
    return list(
        session.scalars(
            select(CorporateAction)
            .where(CorporateAction.security_id == security.id)
            .order_by(CorporateAction.ex_date)
        )
    )


@router.get("/funds/{ticker}/holdings")
def fund_holdings(session: SessionDep, ticker: str, as_of: date | None = None) -> list[dict]:
    """A tracked index ETF's holdings in its latest N-PORT report public on `as_of`
    (point-in-time index membership and weights), e.g. /funds/IVV/holdings for the S&P 500."""
    rows = funds.members(session, ticker, as_of)
    if not rows:
        raise HTTPException(404, f"no holdings for {ticker}")
    return rows
