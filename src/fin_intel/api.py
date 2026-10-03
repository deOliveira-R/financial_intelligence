import secrets
from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import ingest, portfolio, timeseries
from fin_intel.config import get_settings
from fin_intel.db import get_session
from fin_intel.fundamentals import Fact, derive_q4, latest_per_period, split_adjust
from fin_intel.models import (
    Account,
    Concept,
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    Filing,
    FiscalCalendar,
    Security,
    SyncRun,
    SyncState,
    TickerHistory,
)
from fin_intel.models import Fact as FactRow
from fin_intel.prices import adjustments


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """With FI_API_KEY set, every endpoint but /health needs the header X-API-Key."""
    expected = get_settings().api_key
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(401, "missing or invalid X-API-Key", {"WWW-Authenticate": "ApiKey"})


app = FastAPI(title="Financial Intelligence API", version="0.2.0")
api = APIRouter(dependencies=[Depends(require_api_key)])

SessionDep = Annotated[Session, Depends(get_session)]


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


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


class ConceptOut(BaseModel):
    taxonomy: str
    concept: str
    label: str | None
    unit: str
    facts: int


class FactOut(Orm):
    unit: str
    period_start: date
    period_end: date
    period_type: str | None
    value: float
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    filed: date | None
    accession: str | None
    split_adjustment: float
    derived: bool


class FilingOut(Orm):
    accession: str
    form: str | None
    filed: date | None
    report_period_end: date | None
    fiscal_year: int | None
    fiscal_period: str | None


class CalendarOut(Orm):
    segment: int
    year_end_month: int
    year_end_day: int
    year_offset: int
    first_year_end: date
    last_year_end: date


class SeriesOut(Orm):
    id: str
    source: str
    title: str | None
    units: str | None
    frequency: str | None


class ObservationOut(Orm):
    date: date
    value: float | None


class SyncStateOut(Orm):
    provider: str
    dataset: str
    key: str
    last_attempt: datetime
    last_success: datetime | None
    last_error: str | None
    rows: int | None


class SyncRunOut(Orm):
    id: int
    job: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    items_ok: int
    items_failed: int
    message: str | None


class StatusOut(BaseModel):
    recent_runs: list[SyncRunOut]
    failing: list[SyncStateOut]


def _security(session: Session, ticker: str) -> Security:
    security = ingest.get_security(session, ticker)
    if security is None:
        raise HTTPException(404, f"unknown ticker {ticker}")
    return security


def _issuer_cik(session: Session, ticker: str) -> int:
    security = _security(session, ticker)
    if security.cik is None:
        raise HTTPException(404, f"{ticker} has no SEC filings")
    return security.cik


@app.get("/health")  # unauthenticated: for uptime checks
def health() -> dict[str, str]:
    return {"status": "ok"}


@api.get("/status", response_model=StatusOut)
def status(session: SessionDep) -> StatusOut:
    """Recent sync runs and every item whose last sync failed."""
    runs = session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(20))
    failing = session.scalars(
        select(SyncState).where(SyncState.last_error.is_not(None)).order_by(SyncState.key)
    )
    return StatusOut(
        recent_runs=[SyncRunOut.model_validate(r) for r in runs],
        failing=[SyncStateOut.model_validate(s) for s in failing],
    )


@api.get("/securities", response_model=list[SecurityOut])
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


@api.get("/securities/{ticker}", response_model=SecurityDetailOut)
def get_security(session: SessionDep, ticker: str) -> SecurityDetailOut:
    security = _security(session, ticker)
    history = session.scalars(
        select(TickerHistory)
        .where(TickerHistory.security_id == security.id)
        .order_by(TickerHistory.first_seen)
    )
    return SecurityDetailOut(
        **SecurityOut.model_validate(security).model_dump(),
        ticker_history=[TickerHistoryOut.model_validate(h) for h in history],
    )


@api.get("/prices/{ticker}/daily", response_model=list[BarOut])
def daily_prices(
    session: SessionDep,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
    source: str = "tiingo",
) -> list[BarOut]:
    """Unadjusted bars plus split- and dividend-adjusted values computed from stored actions."""
    security = _security(session, ticker)
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


@api.get("/prices/{ticker}/actions", response_model=list[ActionOut])
def corporate_actions(session: SessionDep, ticker: str) -> list[CorporateAction]:
    security = _security(session, ticker)
    return list(
        session.scalars(
            select(CorporateAction)
            .where(CorporateAction.security_id == security.id)
            .order_by(CorporateAction.ex_date)
        )
    )


@api.get("/fundamentals/{ticker}/concepts", response_model=list[ConceptOut])
def fundamental_concepts(session: SessionDep, ticker: str) -> list[ConceptOut]:
    cik = _issuer_cik(session, ticker)
    stmt = (
        select(Concept.taxonomy, Concept.name, Concept.label, FactRow.unit, func.count())
        .join(FactRow, FactRow.concept_id == Concept.id)
        .where(FactRow.cik == cik)
        .group_by(Concept.taxonomy, Concept.name, Concept.label, FactRow.unit)
        .order_by(Concept.taxonomy, Concept.name)
    )
    return [
        ConceptOut(taxonomy=t, concept=c, label=lbl, unit=u, facts=n)
        for t, c, lbl, u, n in session.execute(stmt)
    ]


@api.get("/fundamentals/{ticker}/filings", response_model=list[FilingOut])
def filings(session: SessionDep, ticker: str, form: str | None = None) -> list[Filing]:
    cik = _issuer_cik(session, ticker)
    stmt = select(Filing).where(Filing.cik == cik).order_by(Filing.filed.desc())
    if form:
        stmt = stmt.where(Filing.form == form)
    return list(session.scalars(stmt))


@api.get("/fundamentals/{ticker}/calendar", response_model=list[CalendarOut])
def fiscal_calendar(session: SessionDep, ticker: str) -> list[FiscalCalendar]:
    """The inferred fiscal year end(s); more than one row means the company changed it."""
    cik = _issuer_cik(session, ticker)
    return list(
        session.scalars(
            select(FiscalCalendar).where(FiscalCalendar.cik == cik).order_by(FiscalCalendar.segment)
        )
    )


@api.get("/fundamentals/{ticker}", response_model=list[FactOut])
def fundamentals(
    session: SessionDep,
    ticker: str,
    concept: str,
    unit: str | None = Query(None, description="e.g. USD, USD/shares, shares; default: all"),
    period_type: str | None = Query(
        None, description="annual, quarter, half, nine_months, instant or other"
    ),
    form: str | None = Query(None, description="e.g. 10-K or 10-Q"),
    as_reported: bool = Query(
        False, description="Every filing's value as filed, instead of the latest per period"
    ),
    split_adjusted: bool = Query(
        True, description="Restate share counts and per-share values for later stock splits"
    ),
    fill_q4: bool = Query(
        True, description="Derive missing Q4 values as FY minus 9M (monetary flows only)"
    ),
) -> list[Fact]:
    security = _security(session, ticker)
    if security.cik is None:
        raise HTTPException(404, f"{ticker} has no SEC filings")
    stmt = (
        select(
            Concept.name,
            FactRow.unit,
            FactRow.period_start,
            FactRow.period_end,
            FactRow.period_type,
            FactRow.value,
            FactRow.fiscal_year,
            FactRow.fiscal_period,
            Filing.form,
            Filing.filed,
            Filing.accession,
        )
        .join(Concept, Concept.id == FactRow.concept_id)
        .join(Filing, Filing.id == FactRow.filing_id)
        .where(FactRow.cik == security.cik, Concept.name == concept)
    )
    if unit:
        stmt = stmt.where(FactRow.unit == unit)
    if form:
        stmt = stmt.where(Filing.form == form)
    facts = [Fact.from_row(r) for r in session.execute(stmt)]

    if as_reported:
        facts.sort(key=lambda f: (f.unit, f.period_end, f.period_start, f.filed or date.min))
    else:
        facts = latest_per_period(facts)
        if fill_q4:
            facts = derive_q4(facts)
    if split_adjusted:
        splits = session.execute(
            select(CorporateAction.ex_date, CorporateAction.value)
            .where(CorporateAction.security_id == security.id, CorporateAction.action == "split")
            .distinct()
        ).all()
        facts = split_adjust(facts, [tuple(s) for s in splits])
    if period_type:
        facts = [f for f in facts if f.period_type == period_type]
    return facts


@api.get("/economic/{series_id}", response_model=SeriesOut)
def economic_series(session: SessionDep, series_id: str) -> EconomicSeries:
    series = session.get(EconomicSeries, series_id.upper())
    if series is None:
        raise HTTPException(404, f"unknown series {series_id}")
    return series


@api.get("/economic/{series_id}/observations", response_model=list[ObservationOut])
def economic_observations(
    session: SessionDep, series_id: str, start: date | None = None, end: date | None = None
) -> list[EconomicObservation]:
    stmt = (
        select(EconomicObservation)
        .where(EconomicObservation.series_id == series_id.upper())
        .order_by(EconomicObservation.date)
    )
    if start:
        stmt = stmt.where(EconomicObservation.date >= start)
    if end:
        stmt = stmt.where(EconomicObservation.date <= end)
    return list(session.scalars(stmt))


# --- portfolio (personal data: always behind the API key when one is configured) ----------


class AccountOut(Orm):
    id: int
    name: str
    broker: str
    account_type: str
    taxable: bool
    number_last4: str | None


@api.get("/portfolio/accounts", response_model=list[AccountOut])
def portfolio_accounts(session: SessionDep) -> list[Account]:
    return list(session.scalars(select(Account).order_by(Account.name)))


@api.get("/portfolio/positions")
def portfolio_positions(session: SessionDep) -> list[portfolio.Position]:
    """Holdings per account with cost, value and unrealized gain split by holding term;
    `reconciled` compares the derived quantity with the broker's latest snapshot."""
    return portfolio.positions(session)


@api.get("/portfolio/lots")
def portfolio_lots(session: SessionDep) -> list[portfolio.LotView]:
    return portfolio.lot_views(session)


@api.get("/portfolio/realized")
def portfolio_realized(session: SessionDep, year: int | None = None) -> list[dict]:
    rows = portfolio.lot_book(session).realized
    return [{**asdict(r), "gain": r.gain} for r in rows if year is None or r.sold.year == year]


@api.get("/portfolio/harvest")
def portfolio_harvest(
    session: SessionDep, min_loss: float = 0.0, min_loss_pct: float = 0.0
) -> list[portfolio.HarvestCandidate]:
    """Taxable lots with unrealized losses; `blocking_purchases` would wash the loss."""
    return portfolio.harvest_candidates(session, min_loss, min_loss_pct)


@api.get("/portfolio/context/{ticker}")
def portfolio_context(session: SessionDep, ticker: str) -> portfolio.Context:
    """Price vs its 52-week range and 200-day average, and returns vs SPY."""
    result = portfolio.context(session, ticker)
    if result is None:
        raise HTTPException(404, f"no price history for {ticker}")
    return result


@api.get("/portfolio/replacements/{ticker}")
def portfolio_replacements(
    session: SessionDep, ticker: str, top: int = Query(10, le=50)
) -> list[portfolio.Replacement]:
    """Liquid ETFs that track `ticker` most closely (lowest tracking error): exposure to
    keep while a harvested loss waits out the wash-sale window. One tracking the same
    index may count as substantially identical."""
    return portfolio.replacements(session, ticker, top)


# --- research time series ----------------------------------------------------------------


@api.get("/timeseries", response_model=None)
def timeseries_endpoint(
    session: SessionDep,
    s: Annotated[list[str], Query(description="Series specs, e.g. px:SPY|sma:200, fred:T10Y2Y")],
    start: date | None = None,
    end: date | None = None,
    pit: bool = Query(True, description="FRED values as known on each date (no look-ahead)"),
    format: str = Query("json", pattern="^(json|csv)$"),
):
    """Prices, macro series and indicators aligned on the trading calendar.

    Spec syntax: `px:TICKER` (adjusted close), `close:`, `volume:`, `fred:ID`; one ratio
    (`/`) or difference (`-`) of two terms; then transforms with `|`: sma:N ema:N rsi:N
    macd ret:N diff:N vol:N z:N high:N low:N dd:N yoy.
    """
    from fastapi.responses import PlainTextResponse

    try:
        dates, series = timeseries.build(session, s, start, end, pit)
    except timeseries.SpecError as exc:
        raise HTTPException(400, str(exc)) from None
    if format == "csv":
        lines = [",".join(["date", *(f'"{k}"' for k in series)])]
        for i, d in enumerate(dates):
            cells = ["" if v[i] is None else f"{v[i]:.6g}" for v in series.values()]
            lines.append(",".join([d.isoformat(), *cells]))
        return PlainTextResponse("\n".join(lines) + "\n", media_type="text/csv")
    return {"dates": dates, "series": series}


app.include_router(api)
