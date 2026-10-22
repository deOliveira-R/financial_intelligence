"""Fama-French factor returns from Kenneth French's data library, daily since 1926: the
market over T-bills, size, value, profitability, investment and momentum, and the T-bill
return itself. Survivorship-free and the standard benchmark for "beat the obvious"
(research.md, rule 7): a signal's return is judged after what these factors explain.

Stored as economic series (source "kenfrench"): `FF_MKT_RF`, `FF_SMB`, `FF_HML`, `FF_RMW`,
`FF_CMA`, `FF_MOM`, `FF_RF`, in percent per day. A day's return is known after the close
(the library itself publishes with weeks of lag, so these are benchmarks, not signals).
Each file is complete (a snapshot); the latest download replaces the earlier ones.
"""

import csv
import io
import logging
import zipfile
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import indicators, registry, timeseries
from fin_intel.db import upsert
from fin_intel.models import EconomicObservation, EconomicSeries
from fin_intel.pipeline import tracked
from fin_intel.providers import KenFrenchProvider
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

# file -> its columns' series ids (the CSV header names them in this order)
FILES: dict[str, dict[str, str]] = {
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip": {
        "Mkt-RF": "FF_MKT_RF",
        "SMB": "FF_SMB",
        "HML": "FF_HML",
        "RMW": "FF_RMW",
        "CMA": "FF_CMA",
        "RF": "FF_RF",
    },
    # The three-factor file reaches back to 1926; it only fills days the five-factor file
    # (from 1963) doesn't have, and its SMB/HML differ slightly in construction, so it is
    # loaded first and overwritten where both exist.
    "F-F_Research_Data_Factors_daily_CSV.zip": {
        "Mkt-RF": "FF_MKT_RF",
        "SMB": "FF_SMB",
        "HML": "FF_HML",
        "RF": "FF_RF",
    },
    "F-F_Momentum_Factor_daily_CSV.zip": {"Mom": "FF_MOM"},
}
ORDER = (
    "F-F_Research_Data_Factors_daily_CSV.zip",
    "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip",
    "F-F_Momentum_Factor_daily_CSV.zip",
)
SERIES = {
    "FF_MKT_RF": "Market return over the one-month T-bill (Fama-French), daily, %",
    "FF_SMB": "Small minus big (size factor), daily, %",
    "FF_HML": "High minus low book-to-market (value factor), daily, %",
    "FF_RMW": "Robust minus weak profitability, daily, %",
    "FF_CMA": "Conservative minus aggressive investment, daily, %",
    "FF_MOM": "Momentum factor (prior 12-2 month return), daily, %",
    "FF_RF": "One-month T-bill return, daily, %",
}
ALIASES = {s[3:].lower(): s for s in SERIES}  # mkt_rf, smb, hml, rmw, cma, mom, rf


def resolve(ident: str) -> str:
    key = ident.lower().replace("-", "_")
    if key in ALIASES:
        return ALIASES[key]
    if ident.upper() in SERIES:
        return ident.upper()
    raise ValueError(f"unknown Fama-French series {ident!r}; one of {', '.join(ALIASES)}")


def series_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": s,
            "source": "kenfrench",
            "title": t,
            "units": "Percent",
            "frequency": "D",
            "seasonal_adjustment": "NSA",
            "last_updated": None,
        }
        for s, t in SERIES.items()
    ]


def parse_file(name: str, body: bytes) -> list[dict[str, Any]]:
    """The zip's CSV: a preamble, a header row whose first cell is empty, daily rows
    `YYYYMMDD, v1, v2...`, then a blank line (and sometimes annual tables) we stop at."""
    columns = FILES[name]
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")
    out: list[dict[str, Any]] = []
    header: list[str] | None = None
    for row in csv.reader(io.StringIO(text)):
        cells = [c.strip() for c in row]
        if header is None:
            if cells and cells[0] == "" and any(c in columns for c in cells[1:]):
                header = cells
            continue
        if not cells or not cells[0]:
            break  # the daily table ends at the first blank line
        try:
            day = datetime.strptime(cells[0], "%Y%m%d").date()
        except ValueError:
            break
        for name_, value in zip(header[1:], cells[1:], strict=False):
            series_id = columns.get(name_)
            if series_id is None:
                continue
            try:
                v = float(value)
            except ValueError:
                continue
            if v > -99:  # -99.99 marks a missing value
                out.append({"series_id": series_id, "date": day, "value": v})
    return out


def load_file(session: Session, name: str, body: bytes) -> int:
    if name not in FILES:
        return 0
    upsert(session, EconomicSeries, series_rows(), key=["id"])
    return upsert(session, EconomicObservation, parse_file(name, body), key=["series_id", "date"])


def sync_factors(session: Session, provider: KenFrenchProvider) -> int:
    """Download the three factor files (small zips) and load them, in the order that lets
    the five-factor file overwrite the three-factor one where both have a day."""
    rows = 0
    for name in ORDER:
        with tracked(session, "kenfrench", "file", name) as result:
            session.commit()  # fetch-then-load
            body = provider.fetch_file(name)
            result["rows"] = load_file(session, name, body)
            session.commit()
        rows += result["rows"]
    return rows


def known_on(day: date) -> date:
    """A day's return is known after that day's close."""
    return day + timedelta(days=1)


registry.register(
    Dataset(
        "kenfrench",
        "file",
        lambda s, k, p, t: load_file(s, k, p),
        ("economic",),
        payload="bytes",
        snapshot=True,
        note="Fama-French factor returns, one zipped CSV per file",
    )
)


@timeseries.source("ff")
def _spec(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    """`ff:mkt_rf`, `ff:smb`, `ff:mom`... daily factor returns in percent; a day's value is
    known the next day."""
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
        raise timeseries.SpecError(f"no data for {series_id}; run fin-intel sync-factors")
    by_date = {(known_on(d) if pit else d): v for d, v in rows}
    return [by_date.get(d) for d in days]
