"""Cboe's volatility and option-strategy indices, daily history from Cboe's public files:
the VIX family (VIX since 1990, VIX9D, VIX3M, VIX6M, VVIX), SKEW (since 1990), and the
strategy benchmarks that track systematic option writing on the S&P 500 (PUT: cash-secured
at-the-money puts since 1991, BXM: covered calls, BXMD: 30-delta covered calls, CLL: a
collar, CNDR: an iron condor). Together they measure the price of index insurance and what
selling it has paid, without any option chain data (research H007).

Stored as economic series (source "cboe"): `CBOE_<SYMBOL>` is the daily close (the only
column the strategy files have). Index closes are known at the close, so a day's value
counts as known that day, unlike the put/call volumes (putcall.py), published afterwards.
Each history file is complete, so the dataset is a snapshot: the latest download replaces
the earlier ones in a rebuild.
"""

import csv
import io
import logging
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import EconomicObservation, EconomicSeries
from fin_intel.pipeline import tracked
from fin_intel.providers import CboeProvider
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

INDICES: dict[str, str] = {
    "VIX": "Cboe Volatility Index (30-day implied volatility of the S&P 500)",
    "VIX9D": "Cboe 9-day volatility index",
    "VIX3M": "Cboe 3-month volatility index",
    "VIX6M": "Cboe 6-month volatility index",
    "VVIX": "Cboe VIX of VIX (implied volatility of VIX options)",
    "SKEW": "Cboe SKEW index (price of S&P 500 tail risk)",
    "PUT": "Cboe S&P 500 PutWrite index (cash-secured at-the-money puts, monthly)",
    "BXM": "Cboe S&P 500 BuyWrite index (at-the-money covered calls, monthly)",
    "BXMD": "Cboe S&P 500 30-delta BuyWrite index",
    "CLL": "Cboe S&P 500 95-110 Collar index",
    "CNDR": "Cboe S&P 500 Iron Condor index",
}
SERIES = {f"CBOE_{symbol}": title for symbol, title in INDICES.items()}


def resolve(ident: str) -> str | None:
    """`vix`, `put`... to its series id, or None if it isn't one of these indices."""
    symbol = ident.upper()
    return f"CBOE_{symbol}" if symbol in INDICES else None


def series_rows() -> list[dict[str, Any]]:
    return [
        {
            "id": series_id,
            "source": "cboe",
            "title": title,
            "units": "Index",
            "frequency": "D",
            "seasonal_adjustment": "NSA",
            "last_updated": None,
        }
        for series_id, title in SERIES.items()
    ]


def parse_history(symbol: str, body: bytes) -> list[dict[str, Any]]:
    """`DATE,OPEN,HIGH,LOW,CLOSE` (the volatility indices) or `DATE,<SYMBOL>` (SKEW and
    the strategy benchmarks); dates as MM/DD/YYYY. Rows without a usable value are skipped."""
    reader = csv.reader(io.StringIO(body.decode("utf-8", errors="replace")))
    header = next(reader, None)
    if not header:
        return []
    columns = [c.strip().upper() for c in header]
    value_column = columns.index("CLOSE") if "CLOSE" in columns else 1
    series_id = f"CBOE_{symbol.upper()}"
    out = []
    for row in reader:
        try:
            day = datetime.strptime(row[0].strip(), "%m/%d/%Y").date()
            value = float(row[value_column])
        except ValueError, IndexError:
            continue
        if value > 0:
            out.append({"series_id": series_id, "date": day, "value": value})
    return out


def load_history(session: Session, symbol: str, body: bytes) -> int:
    upsert(session, EconomicSeries, series_rows(), key=["id"])
    return upsert(
        session, EconomicObservation, parse_history(symbol, body), key=["series_id", "date"]
    )


def sync_volatility(session: Session, cboe: CboeProvider, symbols: tuple[str, ...] = ()) -> int:
    """Download each index's full history (small files, one request each) and load it."""
    rows = 0
    for symbol in symbols or tuple(INDICES):
        with tracked(session, "cboe", "index_history", symbol) as result:
            session.commit()  # fetch-then-load
            body = cboe.fetch_index_history(symbol)
            result["rows"] = load_history(session, symbol, body)
            session.commit()
        rows += result["rows"]
    return rows


def known_on(day: date) -> date:
    """An index close is known at the close."""
    return day


registry.register(
    Dataset(
        "cboe",
        "index_history",
        lambda s, k, p, t: load_history(s, k, p),
        ("economic",),
        payload="bytes",
        snapshot=True,
        note="Cboe volatility and strategy index histories (one file per index)",
    )
)
