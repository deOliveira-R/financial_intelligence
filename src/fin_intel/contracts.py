"""Federal contract obligations by industry (NAICS), monthly since October 2007, from
USAspending. A policy signal at the industry level: defense procurement (aircraft 336411,
guided missiles 336414, shipbuilding 336611), R&D (5417), health (3254, 6221), construction
and energy, alongside congressional trades and lobbying.

Point in time: agencies report obligations within weeks, the Department of Defense with a
90-day delay, and figures keep being revised. A month counts as known AVAILABLE_AFTER its
end, and months younger than SETTLED_AFTER are refetched on every sync.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import FederalObligation

FIRST_MONTH = date(2007, 10, 1)  # USAspending searches start in fiscal 2008
AVAILABLE_AFTER = timedelta(days=90)
SETTLED_AFTER = timedelta(days=180)


def month_end(month: date) -> date:
    return (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def available_on(month: date) -> date:
    return month_end(month) + AVAILABLE_AFTER


def months(until: date) -> list[date]:
    """Every month from FIRST_MONTH through the last full month before `until`."""
    out, m = [], FIRST_MONTH
    while month_end(m) < until:
        out.append(m)
        m = month_end(m) + timedelta(days=1)
    return out


def load_page(session: Session, key: str, payload: dict[str, Any]) -> int:
    """One page of a month's obligations (key `2025-01|3`)."""
    month = date.fromisoformat(key.split("|")[0] + "-01")
    rows = [
        {"month": month, "naics": r["code"], "name": r.get("name"), "amount": r["amount"]}
        for r in payload.get("results") or []
        if r.get("code") and r.get("amount") is not None
    ]
    return upsert(session, FederalObligation, rows, key=["month", "naics"])


def series(session: Session, prefix: str) -> list[tuple[date, float]]:
    """Monthly obligations summed over NAICS codes starting with `prefix` (e.g. 3364:
    aerospace products and parts)."""
    rows = session.execute(
        select(FederalObligation.month, func.sum(FederalObligation.amount))
        .where(FederalObligation.naics.startswith(prefix))
        .group_by(FederalObligation.month)
        .order_by(FederalObligation.month)
    )
    return [(m, v) for m, v in rows]
