"""Short interest: shares sold short and not yet covered, per stock, twice a month (FINRA,
since December 2017). Days to cover (short position / average daily volume) measures how
crowded a short is; a crowded short in a rising stock can squeeze.

Settlement dates are mid-month and month-end (moved earlier for holidays); FINRA
publishes about seven business days later, so a position counts as known
PUBLICATION_LAG business days after its settlement date.
"""

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import indicators, registry, timeseries
from fin_intel.db import upsert
from fin_intel.models import ShortInterest, SyncState
from fin_intel.pipeline import tracked
from fin_intel.providers import FinraProvider, massive
from fin_intel.registry import Dataset
from fin_intel.symbols import by_symbol

log = logging.getLogger(__name__)

FIRST = date(2017, 12, 1)
PUBLICATION_LAG = 8  # business days


def available_on(settlement: date) -> date:
    day, left = settlement, PUBLICATION_LAG
    while left:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def candidates(year: int, month: int) -> tuple[list[date], list[date]]:
    """Possible settlement dates for a month's two cycles, most likely first: the 15th and
    the last day, or the business days before them."""
    end = (date(year, month, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    def back(day: date) -> list[date]:
        days = [day - timedelta(days=i) for i in range(6)]
        return [d for d in days if d.weekday() < 5]

    return back(date(year, month, 15)), back(end)


def parse(payload: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for r in payload or []:
        symbol, settled = r.get("symbolCode"), r.get("settlementDate")
        if not symbol or not settled or r.get("currentShortPositionQuantity") is None:
            continue
        rows.append(
            {
                "symbol": massive.normalize_symbol(symbol.strip().upper()),
                "settlement_date": date.fromisoformat(settled),
                "short_position": float(r["currentShortPositionQuantity"]),
                "average_daily_volume": r.get("averageDailyVolumeQuantity"),
                "days_to_cover": r.get("daysToCoverQuantity"),
                "market": r.get("marketClassCode"),
            }
        )
    return rows


# --- loading and syncs ---


def load_short_interest(session: Session, payload: Any) -> int:
    rows = by_symbol(session, parse(payload), "settlement_date")
    return upsert(session, ShortInterest, rows, key=["security_id", "settlement_date"])


def sync_short_interest(session: Session, finra: FinraProvider, settlement: date) -> int:
    """Every stock's short position on one settlement date (a few pages)."""
    with tracked(session, "finra", "short_interest", settlement.isoformat()) as result:
        session.commit()  # fetch-then-load
        total = finra.records_on(settlement)
        pages = [
            finra.fetch_short_interest(settlement, offset)
            for offset in range(0, total, finra.page_size)
        ]
        result["rows"] = sum(load_short_interest(session, p) for p in pages)
    return result["rows"]


SHORT_INTEREST_SETTLED = timedelta(days=30)  # FINRA revisions come within weeks


def short_interest_due(session: Session, finra: FinraProvider, today: date) -> list[date]:
    """Settlement dates to load: each published half-month cycle not loaded yet (found by
    probing its candidate dates), and recent ones loaded before revisions settled."""
    loaded = {
        date.fromisoformat(k): ok.date()
        for k, ok in session.execute(
            select(SyncState.key, SyncState.last_success).where(
                SyncState.provider == "finra",
                SyncState.dataset == "short_interest",
                SyncState.last_success.is_not(None),
            )
        )
    }
    due = [d for d, ok in loaded.items() if ok - d < SHORT_INTEREST_SETTLED]
    year, month = FIRST.year, FIRST.month
    while date(year, month, 1) <= today:
        for cycle in candidates(year, month):
            if any(d in loaded for d in cycle):
                continue
            for d in cycle:
                if available_on(d) > today:
                    break  # not published yet
                if finra.records_on(d):
                    due.append(d)
                    break
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return sorted(due)


registry.register(
    Dataset(
        "finra",
        "short_interest",
        lambda s, k, p, t: load_short_interest(s, p),
        ("shorts",),
        snapshot=True,
        note="per date and page; revised for weeks",
    ),
)


@timeseries.source("si")
def _spec(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    """`si:GME` short position (shares), `si:GME:dtc` days to cover."""
    from fin_intel.symbols import get_security

    ticker, _, field = ident.partition(":")
    column = {"": ShortInterest.short_position, "DTC": ShortInterest.days_to_cover}.get(field)
    if column is None:
        raise timeseries.SpecError(
            f"si:{ident}: field is empty (shares short) or dtc (days to cover)"
        )
    security = get_security(session, ticker)
    if security is None:
        raise timeseries.SpecError(f"unknown ticker {ticker}")
    rows = session.execute(
        select(ShortInterest.settlement_date, column)
        .where(ShortInterest.security_id == security.id, column.is_not(None))
        .order_by(ShortInterest.settlement_date)
    ).all()
    if not rows:
        raise timeseries.SpecError(f"no short interest for {ticker}; run sync-short-interest")
    return timeseries.step([(available_on(d) if pit else d, v) for d, v in rows], days)
