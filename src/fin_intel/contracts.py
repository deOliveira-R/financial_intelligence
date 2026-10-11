"""Federal contract obligations by industry (NAICS), monthly since October 2007, from
USAspending. A policy signal at the industry level: defense procurement (aircraft 336411,
guided missiles 336414, shipbuilding 336611), R&D (5417), health (3254, 6221), construction
and energy, alongside congressional trades and lobbying.

Point in time: agencies report obligations within weeks, the Department of Defense with a
90-day delay, and figures keep being revised. A month counts as known AVAILABLE_AFTER its
end, and months younger than SETTLED_AFTER are refetched on every sync.
"""

import logging
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import indicators, registry, timeseries
from fin_intel.db import upsert
from fin_intel.models import FederalObligation, SyncState
from fin_intel.pipeline import tracked
from fin_intel.providers import UsaspendingProvider
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

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


# --- loading and syncs ---


def sync_contracts_month(session: Session, provider: UsaspendingProvider, month: date) -> int:
    """A month's federal contract obligations by NAICS code, every page."""
    with tracked(session, "usaspending", "naics_month", f"{month:%Y-%m}") as result:
        session.commit()  # fetch-then-load
        pages, page = [], 1
        while True:
            payload = provider.fetch_naics_month(month, page)
            pages.append((f"{month:%Y-%m}|{page}", payload))
            if not (payload.get("page_metadata") or {}).get("hasNext"):
                break
            page += 1
        result["rows"] = sum(load_page(session, key, p) for key, p in pages)
    return result["rows"]


def contract_months_due(session: Session, today: date) -> list[date]:
    """Months never loaded, or last loaded before they settled (revisions, DoD's delay)."""
    loaded = {
        k: ok
        for k, ok in session.execute(
            select(SyncState.key, SyncState.last_success).where(
                SyncState.provider == "usaspending",
                SyncState.dataset == "naics_month",
                SyncState.last_success.is_not(None),
            )
        )
    }
    due = []
    for month in months(today):
        ok = loaded.get(f"{month:%Y-%m}")
        if ok is None or ok.date() - month_end(month) < SETTLED_AFTER:
            due.append(month)
    return due


registry.register(
    Dataset(
        "usaspending",
        "naics_month",
        lambda s, k, p, t: load_page(s, k, p),
        ("policy",),
        snapshot=True,
        note="per month and page; months are revised",
    ),
)


@timeseries.source("gov")
def _spec(session: Session, prefix: str, days: list[date], pit: bool) -> indicators.Series:
    """Monthly federal contract obligations for NAICS codes starting with `prefix`."""

    if not prefix.isdigit():
        raise timeseries.SpecError(f"gov:{prefix}: expected a NAICS code or prefix, e.g. gov:3364")
    rows = series(session, prefix)
    if not rows:
        raise timeseries.SpecError(
            f"no federal obligations for NAICS {prefix}*; run sync-contracts"
        )
    return timeseries.step([(available_on(m) if pit else m, v) for m, v in rows], days)
