"""Short interest: shares sold short and not yet covered, per stock, twice a month (FINRA,
since December 2017). Days to cover (short position / average daily volume) measures how
crowded a short is; a crowded short in a rising stock can squeeze.

Settlement dates are mid-month and month-end (moved earlier for holidays); FINRA
publishes about seven business days later, so a position counts as known
PUBLICATION_LAG business days after its settlement date.
"""

from datetime import date, timedelta
from typing import Any

from fin_intel.providers import massive

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
