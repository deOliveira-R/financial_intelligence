import secrets
from dataclasses import asdict
from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import (
    backtest,
    carry,
    congress,
    cot,
    events,
    filing_text,
    funds,
    ingest,
    insiders,
    portfolio,
    releases,
    screener,
    thirteenf,
    timeseries,
)
from fin_intel.config import get_settings
from fin_intel.db import get_session
from fin_intel.fundamentals import Fact, derive_q4, latest_per_period, split_adjust
from fin_intel.models import (
    Account,
    CompanyMetrics,
    Concept,
    CorporateAction,
    CusipMapping,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    Filing,
    FiscalCalendar,
    InsiderTransaction,
    InstitutionalFiler,
    InstitutionalPosition,
    Security,
    StatementItem,
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


# --- screener ----------------------------------------------------------------------------


@api.get("/screener")
def screener_endpoint(
    session: SessionDep,
    where: Annotated[list[str] | None, Query(description="e.g. pe<15, roic>=0.15")] = None,
    sort: str | None = Query(None, description="metric, '-' prefix for descending"),
    rank: str | None = Query(None, description="'magic': earnings yield + ROIC ranks"),
    preset: str | None = Query(None, description="magic, deep_value, quality, cash_cows"),
    limit: int = Query(50, le=500),
    sector: Annotated[list[str] | None, Query(description="only these sectors")] = None,
    exclude_sector: Annotated[list[str] | None, Query(description="e.g. finance")] = None,
) -> dict:
    """Companies matching every filter on their latest metrics. Sectors come from SIC
    codes: agriculture, mining, construction, manufacturing, transportation, utilities,
    wholesale, retail, finance, services, public."""
    try:
        as_of, rows = screener.screen(
            session, where, sort, rank, preset, limit, sector, exclude_sector
        )
    except screener.ScreenError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"as_of": as_of, "count": len(rows), "results": rows}


# --- statements and company metrics --------------------------------------------------------


@api.get("/fundamentals/{ticker}/statements")
def financial_statements(
    session: SessionDep,
    ticker: str,
    statement: str = Query("income", pattern="^(income|balance|cash_flow)$"),
    period: str = Query("annual", pattern="^(annual|quarter)$"),
    limit: int = Query(8, le=40),
) -> dict:
    """Standard statement lines per period, newest first. Balance-sheet lines are taken at
    each period's end date; `concepts` names the XBRL concept behind each line."""
    from fin_intel.statements import LINE_ITEMS

    cik = _issuer_cik(session, ticker)
    lines = [name for name, (stmt, _, _) in LINE_ITEMS.items() if stmt == statement]
    rows = session.execute(
        select(StatementItem, Concept.taxonomy, Concept.name)
        .join(Concept, Concept.id == StatementItem.concept_id)
        .where(StatementItem.cik == cik, StatementItem.line_item.in_(lines))
    ).all()
    periods: dict[tuple, dict] = {}
    for item, taxonomy, name in rows:
        if statement == "balance":
            # Balances are instants; annual means those at a fiscal year end.
            if item.period_type != "instant" or (period == "annual" and item.fiscal_period != "FY"):
                continue
        elif item.period_type != period:
            continue
        entry = periods.setdefault(
            (item.period_start, item.period_end),
            {
                "period_start": item.period_start,
                "period_end": item.period_end,
                "fiscal_year": item.fiscal_year,
                "fiscal_period": item.fiscal_period,
                "concepts": {},
            },
        )
        entry[item.line_item] = item.value
        entry["concepts"][item.line_item] = f"{taxonomy}:{name}"
    ordered = sorted(periods.values(), key=lambda e: e["period_end"], reverse=True)[:limit]
    return {"ticker": ticker.upper(), "statement": statement, "period": period, "periods": ordered}


@api.get("/metrics/{ticker}")
def company_metrics(session: SessionDep, ticker: str, history: int = Query(0, le=365)) -> dict:
    """Latest valuation, quality, growth and score metrics, plus up to `history` earlier
    daily rows (each computed from what was known that day)."""
    from fin_intel.screener import NUMERIC

    security = _security(session, ticker)
    rows = list(
        session.scalars(
            select(CompanyMetrics)
            .where(CompanyMetrics.security_id == security.id)
            .order_by(CompanyMetrics.as_of.desc())
            .limit(history + 1)
        )
    )
    if not rows:
        raise HTTPException(404, f"no metrics for {ticker} (not a primary listed security?)")

    def as_dict(m: CompanyMetrics) -> dict:
        return {"as_of": m.as_of, "period_end": m.period_end, **{c: getattr(m, c) for c in NUMERIC}}

    return {
        "ticker": ticker.upper(),
        "latest": as_dict(rows[0]),
        "history": [as_dict(m) for m in rows[1:]],
    }


# --- insiders ------------------------------------------------------------------------------


class InsiderOut(Orm):
    filing_date: date | None
    trans_date: date
    owner_name: str | None
    relationship: str | None
    owner_title: str | None
    trans_code: str | None
    acquired_disposed: str | None
    shares: float | None
    price: float | None
    shares_after: float | None
    direct_indirect: str | None
    plan_10b5_1: bool
    accession: str


@api.get("/insiders/clusters")
def insider_clusters(
    session: SessionDep,
    days: int = Query(30, le=365),
    min_insiders: int = Query(3, ge=2),
) -> list[insiders.ClusterBuy]:
    """Companies where several insiders bought on the open market (not under 10b5-1 plans)
    within the window."""
    return insiders.cluster_buys(session, days, min_insiders)


@api.get("/insiders/{ticker}", response_model=list[InsiderOut])
def insider_transactions(
    session: SessionDep,
    ticker: str,
    code: str | None = Query(None, description="e.g. P (purchases), S (sales)"),
    limit: int = Query(100, le=1000),
) -> list[InsiderTransaction]:
    """A company's insider transactions, newest first."""
    cik = _issuer_cik(session, ticker)
    stmt = (
        select(InsiderTransaction)
        .where(InsiderTransaction.issuer_cik == cik)
        .order_by(InsiderTransaction.trans_date.desc())
        .limit(limit)
    )
    if code:
        stmt = stmt.where(InsiderTransaction.trans_code == code.upper())
    return list(session.scalars(stmt))


# --- institutional holdings ------------------------------------------------------------------


@api.get("/holdings/managers")
def holdings_managers(session: SessionDep, q: str, limit: int = Query(20, le=100)) -> list[dict]:
    """Find 13F filers by name, e.g. q=berkshire, q=pershing, q=scion."""
    return [{"cik": f.cik, "name": f.name} for f in thirteenf.find_filers(session, q, limit)]


@api.get("/holdings/managers/{cik}")
def holdings_manager(session: SessionDep, cik: int, period: date | None = None) -> dict:
    """A manager's positions at a quarter end, with the change since its previous report
    (new, added, reduced, sold, unchanged), largest first."""
    current, changes = thirteenf.manager_changes(session, cik, period)
    if current is None:
        raise HTTPException(404, f"no 13F holdings for CIK {cik}")
    return {"cik": cik, "period": current, "positions": changes}


@api.get("/holdings/security/{ticker}")
def holdings_security(session: SessionDep, ticker: str, limit: int = Query(25, le=200)) -> dict:
    """Institutions holding a security at the latest quarter end, largest first."""
    security = _security(session, ticker)
    cusips = list(
        session.scalars(select(CusipMapping.cusip).where(CusipMapping.security_id == security.id))
    )
    if not cusips:
        raise HTTPException(404, f"no 13F holdings mapped to {ticker}")
    period = session.scalar(
        select(func.max(InstitutionalPosition.period)).where(
            InstitutionalPosition.cusip.in_(cusips)
        )
    )
    rows = session.execute(
        select(InstitutionalPosition, InstitutionalFiler.name)
        .join(InstitutionalFiler, InstitutionalFiler.cik == InstitutionalPosition.filer_cik)
        .where(
            InstitutionalPosition.cusip.in_(cusips),
            InstitutionalPosition.period == period,
            InstitutionalPosition.put_call == "",
        )
        .order_by(InstitutionalPosition.value.desc())
        .limit(limit)
    ).all()
    return {
        "ticker": ticker.upper(),
        "period": period,
        "holders": [
            {
                "cik": p.filer_cik,
                "name": name,
                "shares": p.shares,
                "value": p.value,
                "filed": p.filed,
            }
            for p, name in rows
        ],
    }


# --- congressional trades --------------------------------------------------------------------


@api.get("/congress/trades")
def congress_trades(
    session: SessionDep,
    member: str | None = Query(None, description="Part of a name, e.g. pelosi"),
    ticker: str | None = None,
    since: Annotated[date | None, Query(description="Transaction date on or after")] = None,
    type: str | None = Query(None, description="purchase, sale or exchange"),
    limit: int = Query(200, le=2000),
) -> list[congress.Trade]:
    """Members of Congress's reported trades (PTRs), newest transaction first. Amounts are
    the reported range; `filed` minus `trans_date` is the disclosure lag (up to 45 days)."""
    return congress.trades(
        session, member=member, ticker=ticker, since=since, trans_type=type, limit=limit
    )


@api.get("/congress/popular")
def congress_popular(
    session: SessionDep, days: int = Query(90, le=730), limit: int = Query(50, le=500)
) -> list[congress.Popular]:
    """Tickers traded by the most distinct members within the window."""
    return congress.most_traded(session, days)[:limit]


# --- backtests -----------------------------------------------------------------------------


@api.get("/backtest")
def backtest_rule(
    session: SessionDep,
    asset: str,
    rule: str = Query(..., description="e.g. px:SPY > px:SPY|sma:200"),
    start: date | None = None,
    end: date | None = None,
    cost_bps: float = 5.0,
    short: bool = False,
    curve: bool = Query(False, description="Include the daily equity curves"),
) -> dict:
    """Backtest a rule on point-in-time series: the position decided at each close is held
    over the next day. Returns strategy vs buy-and-hold stats, per-year returns, trades."""
    try:
        r = backtest.run(session, asset, rule, start, end, cost_bps, short)
    except backtest.RuleError as exc:
        raise HTTPException(400, str(exc)) from None
    out = {
        k: v
        for k, v in asdict(r).items()
        if k not in ("dates", "equity", "benchmark_equity", "trade_list")
    }
    out["trade_list"] = [asdict(t) for t in r.trade_list[-50:]]
    if curve:
        out["curve"] = [
            {"date": d, "strategy": e, "benchmark": b}
            for d, e, b in zip(r.dates, r.equity, r.benchmark_equity, strict=True)
        ]
    return out


@api.get("/events/{source}")
def event_study(
    session: SessionDep,
    source: str,
    member: str | None = None,
    min_amount: float = 0,
    cik: int | None = None,
    min_insiders: int = 3,
    item: str | None = Query(None, description="8k: item, e.g. 2.02 (earnings releases)"),
    form: str | None = Query(None, description="8k: form, e.g. SC 13D"),
) -> events.Study:
    """Average returns vs SPY 5, 21, 63 and 126 trading days after disclosed trades:
    source = insiders (cluster buys), congress (purchases), 13f (new positions) or 8k
    (corporate events from SEC filings, filtered by `item` or `form`)."""
    try:
        found = events.from_source(
            session, source, member, min_amount, cik, min_insiders, item, form
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    result = events.study(session, found)
    result.unpriced = result.unpriced[:100]
    return result


# --- economic calendar ----------------------------------------------------------------------


@api.get("/calendar")
def economic_calendar(
    session: SessionDep,
    days: int = Query(30, le=365),
    start: date | None = None,
    daily: bool = False,
) -> list[releases.Event]:
    """Scheduled economic releases (CPI, payrolls, GDP, FOMC decisions...) with the tracked
    series each one updates; `daily=true` adds daily releases (rates, spreads, VIX)."""
    return releases.upcoming(session, days, start, daily)


# --- filing text ----------------------------------------------------------------------------


@api.get("/filings/search")
def filing_search(
    session: SessionDep,
    q: str,
    ticker: str | None = None,
    since: date | None = None,
    limit: int = Query(20, le=200),
) -> list[filing_text.Hit]:
    """Full-text search over 10-K/10-Q risk factors and MD&A and earnings press releases
    (SQLite FTS5 syntax: words, "phrases", OR, NEAR)."""
    ciks = None
    if ticker:
        security = ingest.get_security(session, ticker)
        if security is None or not security.cik:
            raise HTTPException(404, f"unknown ticker {ticker}")
        ciks = [security.cik]
    return filing_text.search(session, q, ciks, since, limit)


@api.get("/filings/{accession}/text")
def filing_sections(session: SessionDep, accession: str, section: str | None = None) -> list[dict]:
    """A filing's stored sections as plain text (section: risk_factors, mdna, EX-99.1...)."""
    rows = filing_text.get(session, accession, section)
    if not rows:
        raise HTTPException(404, f"no text for {accession}")
    return [
        {"section": r.section, "form": r.form, "filed": r.filed, "period": r.period, "text": r.text}
        for r in rows
    ]


# --- index ETF holdings ---------------------------------------------------------------------


@api.get("/funds/{ticker}/holdings")
def fund_holdings(session: SessionDep, ticker: str, as_of: date | None = None) -> list[dict]:
    """A tracked index ETF's holdings in its latest N-PORT report public on `as_of`
    (point-in-time index membership and weights), e.g. /funds/IVV/holdings for the S&P 500."""
    rows = funds.members(session, ticker, as_of)
    if not rows:
        raise HTTPException(404, f"no holdings for {ticker}")
    return rows


# --- yen carry trade ------------------------------------------------------------------------


@api.get("/carry")
def carry_gauge(session: SessionDep, as_of: date | None = None) -> carry.Gauge:
    """Yen carry trade gauge: US-Japan differentials, carry-to-risk, speculators' yen
    positioning, Japanese foreign bond flows, upcoming BOJ/FOMC decisions, and flags."""
    try:
        return carry.gauge(session, as_of)
    except timeseries.SpecError as exc:
        raise HTTPException(404, str(exc)) from None


# --- CFTC positioning ------------------------------------------------------------------------


@api.get("/cot")
def cot_summary(session: SessionDep) -> list[cot.Summary]:
    """Speculative positioning per market at the latest report (managed money for
    commodities, leveraged funds for financials), with its 3-year COT index (0-100)."""
    return cot.summary(session)


@api.get("/cot/{market}")
def cot_market(
    session: SessionDep, market: str, group: str = "managed_money", limit: int = Query(156)
) -> list[dict]:
    """A group's weekly positions in one market, newest first, e.g. /cot/gold, or
    /cot/sp500?group=leveraged (groups: see cot.FIELDS)."""
    try:
        rows = cot.history(session, market, group)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    fields = {f: cot.values(rows, f) for f in ("net", "net_pct_oi", "index")}
    out = [
        {
            "report_date": r.report_date,
            "available_on": cot.available_on(r.report_date),
            "long": r.long,
            "short": r.short,
            "spread": r.spread,
            "open_interest": r.open_interest,
            **{f: v[i] for f, v in fields.items()},
        }
        for i, r in enumerate(rows)
    ]
    return out[::-1][:limit]


app.include_router(api)
