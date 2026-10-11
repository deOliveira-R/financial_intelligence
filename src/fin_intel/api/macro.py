"""Macro and markets: economic series, the release calendar, the carry gauge, CFTC
positioning."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from fin_intel import (
    carry,
    cot,
    releases,
    timeseries,
)
from fin_intel.api.app import Orm, SessionDep
from fin_intel.models import (
    EconomicObservation,
    EconomicSeries,
)

router = APIRouter()


class SeriesOut(Orm):
    id: str
    source: str
    title: str | None
    units: str | None
    frequency: str | None


class ObservationOut(Orm):
    date: date
    value: float | None


@router.get("/economic/{series_id}", response_model=SeriesOut)
def economic_series(session: SessionDep, series_id: str) -> EconomicSeries:
    series = session.get(EconomicSeries, series_id.upper())
    if series is None:
        raise HTTPException(404, f"unknown series {series_id}")
    return series


@router.get("/economic/{series_id}/observations", response_model=list[ObservationOut])
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


@router.get("/calendar")
def economic_calendar(
    session: SessionDep,
    days: int = Query(30, le=365),
    start: date | None = None,
    daily: bool = False,
) -> list[releases.Event]:
    """Scheduled economic releases (CPI, payrolls, GDP, FOMC decisions...) with the tracked
    series each one updates; `daily=true` adds daily releases (rates, spreads, VIX)."""
    return releases.upcoming(session, days, start, daily)


@router.get("/carry")
def carry_gauge(session: SessionDep, as_of: date | None = None) -> carry.Gauge:
    """Yen carry trade gauge: US-Japan differentials, carry-to-risk, speculators' yen
    positioning, Japanese foreign bond flows, upcoming BOJ/FOMC decisions, and flags."""
    try:
        return carry.gauge(session, as_of)
    except timeseries.SpecError as exc:
        raise HTTPException(404, str(exc)) from None


@router.get("/cot")
def cot_summary(session: SessionDep) -> list[cot.Summary]:
    """Speculative positioning per market at the latest report (managed money for
    commodities, leveraged funds for financials), with its 3-year COT index (0-100)."""
    return cot.summary(session)


@router.get("/cot/{market}")
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
