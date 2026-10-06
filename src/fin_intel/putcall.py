"""Options volume and put/call ratios (Cboe), daily since November 2006: a sentiment gauge.
Equity put/call extremes have marked fear (high) and complacency (low); index options are
dominated by hedging.

Stored as economic series (source "cboe"): `CBOE_<PRODUCT>_CALLS`, `_PUTS` (contracts) and
`_PC` (puts / calls), for TOTAL, EQUITY and INDEX since 2006, and ETP, SPX and VIX (options
on the VIX) since October 2019. Published after the close, so a day's figures count as known
the next day.
"""

import csv
import io
from datetime import date, datetime, timedelta
from typing import Any

ARCHIVE_PRODUCTS = ("total", "equity", "index")
ARCHIVE_END = date(2019, 10, 4)
DAILY_START = date(2019, 10, 7)
DAILY_PRODUCTS = {
    "SUM OF ALL PRODUCTS": "TOTAL",
    "EQUITY OPTIONS": "EQUITY",
    "INDEX OPTIONS": "INDEX",
    "EXCHANGE TRADED PRODUCTS": "ETP",
    "SPX + SPXW": "SPX",
    "CBOE VOLATILITY INDEX (VIX)": "VIX",
}
PRODUCTS = tuple(DAILY_PRODUCTS.values())
SERIES = {
    f"CBOE_{p}_{kind}": f"Cboe {p.lower()} options, {label}"
    for p in PRODUCTS
    for kind, label in (("CALLS", "call volume"), ("PUTS", "put volume"), ("PC", "put/call ratio"))
}
ALIASES = {
    f"{p.lower()}_{k}": f"CBOE_{p}_{k.upper()}" for p in PRODUCTS for k in ("calls", "puts", "pc")
}


def resolve(ident: str) -> str:
    if ident.upper() in SERIES:
        return ident.upper()
    if ident.lower() in ALIASES:
        return ALIASES[ident.lower()]
    raise ValueError(f"unknown Cboe series {ident!r}; one of {', '.join(ALIASES)}")


def available_on(day: date) -> date:
    return day + timedelta(days=1)


def series_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": s,
            "source": "cboe",
            "title": t,
            "units": "Ratio" if s.endswith("_PC") else "Contracts",
            "frequency": "D",
            "seasonal_adjustment": "NSA",
            "last_updated": None,
        }
        for s, t in SERIES.items()
    ]


def _observations(product: str, day: date, calls: float, puts: float) -> list[dict[str, Any]]:
    out = [
        {"series_id": f"CBOE_{product}_CALLS", "date": day, "value": calls},
        {"series_id": f"CBOE_{product}_PUTS", "date": day, "value": puts},
    ]
    if calls:
        out.append({"series_id": f"CBOE_{product}_PC", "date": day, "value": puts / calls})
    return out


def parse_archive(product: str, body: bytes) -> list[dict[str, Any]]:
    """totalpc.csv / equitypc.csv / indexpc.csv: a disclaimer, then DATE,CALL(S),PUT(S),..."""
    out = []
    for row in csv.reader(io.StringIO(body.decode("utf-8", errors="replace"))):
        try:
            day = datetime.strptime(row[0].strip(), "%m/%d/%Y").date()
            calls, puts = float(row[1]), float(row[2])
        except ValueError, IndexError:
            continue
        out += _observations(product.upper(), day, calls, puts)
    return out


def parse_daily(day: date, payload: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for name, product in DAILY_PRODUCTS.items():
        volume = next((r for r in payload.get(name) or [] if r.get("name") == "VOLUME"), None)
        if volume and volume.get("call") is not None and volume.get("put") is not None:
            out += _observations(product, day, float(volume["call"]), float(volume["put"]))
    return out
