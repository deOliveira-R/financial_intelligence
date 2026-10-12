"""Private and public credit across ~45 economies (BIS), for the credit-cycle thesis
(docs/research/theses.md, thesis 3: Steve Keen, Minsky).

Banks create money when they lend, so the flow of new private credit is a source of
demand in its own right and its reversal a demand shock. BIS publishes credit by
borrowing sector (households, non-financial corporations, government) back to the 1940s
for some economies, debt service ratios, and credit-to-GDP gaps (the Basel early warning
indicator), which together cover the conditions to test: the debt level, the flow (the
credit impulse), the public offset (government borrowing replacing private) and the
burden (debt service).

Whole datasets are fetched (a few MB each) and stored as generic series; `SERIES` names
the ones research uses. Point in time: BIS releases a quarter about five to six months
after it ends (2026-Q1 was the latest in October 2026), so a quarter counts as public
PUBLICATION_LAG after its end. Revisions aren't versioned.
"""

import csv
import io
import logging
from datetime import date, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel import indicators, registry, timeseries
from fin_intel.db import upsert
from fin_intel.models import BisObservation, BisSeries
from fin_intel.pipeline import tracked
from fin_intel.providers import BisProvider
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

# flow -> (version, dimension columns in key order)
FLOWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "WS_TC": (
        "2.0",
        (
            "FREQ",
            "BORROWERS_CTY",
            "TC_BORROWERS",
            "TC_LENDERS",
            "VALUATION",
            "UNIT_TYPE",
            "TC_ADJUST",
        ),
    ),  # fmt: skip
    "WS_DSR": ("1.0", ("FREQ", "BORROWERS_CTY", "DSR_BORROWERS")),
    "WS_CREDIT_GAP": (
        "1.0",
        ("FREQ", "BORROWERS_CTY", "TC_BORROWERS", "TC_LENDERS", "CG_DTYPE"),
    ),
}
PUBLICATION_LAG = timedelta(days=170)

# name -> (flow, key with {c} for the country)
SERIES: dict[str, tuple[str, str]] = {
    "private_credit_gdp": ("WS_TC", "Q.{c}.P.A.M.770.A"),
    "bank_credit_gdp": ("WS_TC", "Q.{c}.P.B.M.770.A"),
    "household_credit_gdp": ("WS_TC", "Q.{c}.H.A.M.770.A"),
    "corporate_credit_gdp": ("WS_TC", "Q.{c}.N.A.M.770.A"),
    "government_debt_gdp": ("WS_TC", "Q.{c}.G.A.N.770.A"),
    "private_credit": ("WS_TC", "Q.{c}.P.A.M.XDC.A"),  # domestic currency
    "government_debt": ("WS_TC", "Q.{c}.G.A.N.XDC.A"),
    "credit_gap": ("WS_CREDIT_GAP", "Q.{c}.P.A.C"),  # actual minus trend, % of GDP
    "dsr_private": ("WS_DSR", "Q.{c}.P"),  # debt service, % of income
    "dsr_households": ("WS_DSR", "Q.{c}.H"),
    "dsr_corporate": ("WS_DSR", "Q.{c}.N"),
}
# Derived from a credit level and its ratio to GDP (which gives GDP): the change in the
# year's new credit, as a share of GDP. Keen's credit impulse (private) and the public
# counterpart that can offset it.
IMPULSES = {
    "credit_impulse": ("private_credit", "private_credit_gdp"),
    "government_impulse": ("government_debt", "government_debt_gdp"),
}


def quarter_end(period: str) -> date | None:
    """'2026-Q1' -> 2026-03-31."""
    if len(period) != 7 or period[4:6] != "-Q":
        return None
    year, quarter = int(period[:4]), int(period[6])
    return date(year + (quarter == 4), 1 if quarter == 4 else quarter * 3 + 1, 1) - timedelta(1)


def parse(flow: str, text: str) -> tuple[list[dict], list[dict]]:
    dims = FLOWS[flow][1]
    series, observations = {}, []
    for row in csv.DictReader(io.StringIO(text)):
        period = quarter_end(row.get("TIME_PERIOD") or "")
        value = row.get("OBS_VALUE")
        if period is None or value in (None, "", "NaN"):
            continue
        key = ".".join(row[d] for d in dims)
        series[key] = {
            "flow": flow,
            "key": key,
            "country": row["BORROWERS_CTY"],
            "title": row.get("TITLE_TS") or None,
        }
        observations.append({"flow": flow, "key": key, "period": period, "value": float(value)})
    return list(series.values()), observations


def load(session: Session, flow: str, payload: bytes) -> int:
    """A whole dataset, replacing what was stored for it."""
    series, observations = parse(flow, payload.decode("utf-8-sig"))
    if not observations:
        return 0
    session.execute(delete(BisObservation).where(BisObservation.flow == flow))
    session.execute(delete(BisSeries).where(BisSeries.flow == flow))
    upsert(session, BisSeries, series, key=["flow", "key"])
    return upsert(session, BisObservation, observations, key=["flow", "key", "period"])


def sync_bis(session: Session, bis: BisProvider) -> int:
    with tracked(session, "bis", "dataset", "all") as result:
        session.commit()  # fetch-then-load
        payloads = {flow: bis.fetch_dataset(flow, v) for flow, (v, _) in FLOWS.items()}
        rows = 0
        for flow, payload in payloads.items():
            rows += load(session, flow, payload)
            session.commit()
        result["rows"] = rows
    return result["rows"]


def history(session: Session, country: str, name: str) -> list[tuple[date, float]]:
    """A named series (SERIES or IMPULSES) for a country, by quarter end."""
    if name in IMPULSES:
        level, ratio = (dict(history(session, country, n)) for n in IMPULSES[name])
        quarters = sorted(set(level) & set(ratio))
        out = []
        for i in range(8, len(quarters)):
            q, q4, q8 = quarters[i], quarters[i - 4], quarters[i - 8]
            if (q - q8).days > 750 or not ratio[q]:
                continue  # a gap in the series
            gdp = level[q] / (ratio[q] / 100)  # annual GDP in domestic currency
            out.append((q, ((level[q] - level[q4]) - (level[q4] - level[q8])) / gdp * 100))
        return out
    if name not in SERIES:
        raise ValueError(f"unknown series {name}; one of {', '.join([*SERIES, *IMPULSES])}")
    flow, key = SERIES[name]
    return [
        (p, v)
        for p, v in session.execute(
            select(BisObservation.period, BisObservation.value)
            .where(BisObservation.flow == flow, BisObservation.key == key.format(c=country))
            .order_by(BisObservation.period)
        )
    ]


def countries(session: Session) -> list[str]:
    return sorted(set(session.scalars(select(BisSeries.country).distinct())))


@timeseries.source("bis")
def _spec(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    """`bis:US:credit_impulse`, `bis:CN:private_credit_gdp`, `bis:GB:dsr_households`...
    (country code, then a name from SERIES or IMPULSES)."""
    country, _, name = ident.partition(":")
    try:
        rows = history(session, country, name.lower())
    except ValueError as exc:
        raise timeseries.SpecError(str(exc)) from None
    if not rows:
        raise timeseries.SpecError(f"no BIS data for {ident}; run fin-intel sync-bis")
    lag = PUBLICATION_LAG if pit else timedelta(0)
    return timeseries.step([(q + lag, v) for q, v in rows], days)


registry.register(
    Dataset(
        "bis",
        "dataset",
        lambda s, k, p, t: load(s, k.split("|")[0], p),
        ("credit",),
        payload="bytes",
        snapshot=True,
    ),
)
