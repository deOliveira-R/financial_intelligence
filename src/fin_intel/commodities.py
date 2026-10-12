"""Commodity prices from the World Bank's Pink Sheet: monthly averages since 1960 in US
dollars, for the metals (copper, gold, iron ore, aluminium, nickel, silver, zinc) and the
main energy and farm benchmarks.

FRED has copper and iron ore only from 1990-1992 and no gold since the London fixing was
withdrawn in 2022; the Pink Sheet has them all, from 1960, from one official publisher
(CC BY 4.0). A month's average comes out early the next month (the file is updated on
about the second business day), and the prices aren't revised.

Stored as economic series (source "worldbank"); timeseries reads them as `wb:<alias>`.
"""

import io
import re
from datetime import date, timedelta
from typing import Any

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import indicators, registry, timeseries
from fin_intel.db import upsert
from fin_intel.models import EconomicObservation, EconomicSeries
from fin_intel.pipeline import tracked, utc_today
from fin_intel.providers import WorldBankProvider
from fin_intel.registry import Dataset

# Column header in the "Monthly Prices" sheet -> (alias, series id)
COLUMNS = {
    "Copper": ("copper", "WB_COPPER"),
    "Gold": ("gold", "WB_GOLD"),
    "Silver": ("silver", "WB_SILVER"),
    "Iron ore, cfr spot": ("iron_ore", "WB_IRON_ORE"),
    "Aluminum": ("aluminum", "WB_ALUMINUM"),
    "Nickel": ("nickel", "WB_NICKEL"),
    "Zinc": ("zinc", "WB_ZINC"),
    "Crude oil, Brent": ("brent", "WB_BRENT"),
    "Natural gas, US": ("natgas_us", "WB_NATGAS_US"),
    "Coal, Australian": ("coal", "WB_COAL"),
    "Wheat, US HRW": ("wheat", "WB_WHEAT"),
    "Maize": ("maize", "WB_MAIZE"),
    "Soybeans": ("soybeans", "WB_SOYBEANS"),
}
ALIASES = {alias: series_id for alias, series_id in COLUMNS.values()}
PUBLICATION_LAG = timedelta(days=5)  # after the month's end
MONTH = re.compile(r"^(\d{4})M(\d{2})$")


def resolve(ident: str) -> str:
    if ident.upper() in ALIASES.values():
        return ident.upper()
    if ident.lower() in ALIASES:
        return ALIASES[ident.lower()]
    raise ValueError(f"unknown World Bank commodity {ident!r}; one of {', '.join(ALIASES)}")


def available_on(month: date) -> date:
    """A month's average is public a few days after the month ends."""
    following = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return following + PUBLICATION_LAG


def parse(payload: bytes) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(series rows, observations) from the monthly workbook, dated by the month's first
    day. Missing values ("…", blanks) are skipped."""
    workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    rows = list(workbook["Monthly Prices"].iter_rows(values_only=True))
    header = next((i for i, r in enumerate(rows) if r and any(c == "Copper" for c in r)), None)
    if header is None:
        return [], []
    names, units = rows[header], rows[header + 1]
    columns: dict[int, str] = {}
    series = []
    for j, (name, unit) in enumerate(zip(names, units, strict=False)):
        if str(name) not in COLUMNS:
            continue
        columns[j] = COLUMNS[str(name)][1]
        series.append(
            {
                "id": columns[j],
                "source": "worldbank",
                "title": f"{name} (World Bank Pink Sheet, monthly average)",
                "units": str(unit or "").strip("()"),
                "frequency": "M",
                "seasonal_adjustment": "NSA",
                "last_updated": None,
                "release_id": None,
            }
        )
    observations = []
    for row in rows[header + 2 :]:
        found = MONTH.match(str(row[0] or ""))
        if not found:
            continue
        month = date(int(found.group(1)), int(found.group(2)), 1)
        for j, series_id in columns.items():
            value = row[j] if j < len(row) else None
            if isinstance(value, int | float):
                observations.append({"series_id": series_id, "date": month, "value": float(value)})
    return series, observations


def load(session: Session, payload: bytes) -> int:
    series, observations = parse(payload)
    upsert(session, EconomicSeries, series, key=["id"])
    return upsert(session, EconomicObservation, observations, key=["series_id", "date"])


def sync(session: Session, provider: WorldBankProvider) -> int:
    key = utc_today().strftime("%Y-%m")
    with tracked(session, "worldbank", "pinksheet", "monthly") as result:
        session.commit()  # fetch-then-load
        payload = provider.fetch_monthly(key)
        result["rows"] = load(session, payload)
    return result["rows"]


registry.register(
    Dataset(
        "worldbank",
        "pinksheet",
        lambda s, k, p, t: load(s, p),
        ("economic",),
        payload="bytes",
        snapshot=True,
        note="the whole history, each update",
    )
)


@timeseries.source("wb")
def _spec(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    try:
        series_id = resolve(ident)
    except ValueError as exc:
        raise timeseries.SpecError(str(exc)) from None
    rows = session.execute(
        select(EconomicObservation.date, EconomicObservation.value)
        .where(EconomicObservation.series_id == series_id, EconomicObservation.value.is_not(None))
        .order_by(EconomicObservation.date)
    ).all()
    if not rows:
        raise timeseries.SpecError(f"no data for {series_id}; run fin-intel sync-commodities")
    # Dated by the month's end without point in time (like FRED's final values), when known
    # with it.
    known = [(available_on(d) if pit else _month_end(d), v) for d, v in rows]
    return timeseries.step(known, days)


def _month_end(month: date) -> date:
    return available_on(month) - PUBLICATION_LAG - timedelta(days=1)
