"""Long-run macro history: the Jordà-Schularick-Taylor Macrohistory Database, 18 advanced
economies yearly since 1870 (credit to households and businesses, GDP, prices, rates,
public debt, systemic banking crises, equity, housing and bond returns).

For the credit-cycle thesis it multiplies the evidence: about 90 crisis onsets over 150
years, where the BIS quarterly panel since the 1990s has a handful per country. Compiled
after the fact, so it serves research on how economies behaved, not point-in-time
trading; a year's values count as known the following year when used as predictors.
License CC BY-NC-SA 4.0 (personal research only).
"""

import io
from typing import Any

import openpyxl
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import PanelObservation
from fin_intel.pipeline import tracked
from fin_intel.providers.macrohistory import MacrohistoryProvider
from fin_intel.registry import Dataset

SOURCE = "jst"
SKIP = {"year", "country", "iso", "ifs"}


def parse(payload: bytes) -> list[dict[str, Any]]:
    workbook = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
    rows = workbook.worksheets[0].iter_rows(values_only=True)
    head = [str(h) for h in next(rows)]
    out = []
    for row in rows:
        record = dict(zip(head, row, strict=False))
        if not record.get("iso") or not record.get("year"):
            continue
        for variable, value in record.items():
            if variable in SKIP or not isinstance(value, int | float):
                continue
            out.append(
                {
                    "source": SOURCE,
                    "country": record["iso"],
                    "variable": variable,
                    "year": int(record["year"]),
                    "value": float(value),
                }
            )
    return out


def load(session: Session, payload: bytes) -> int:
    rows = parse(payload)
    if not rows:
        return 0
    session.execute(delete(PanelObservation).where(PanelObservation.source == SOURCE))
    return upsert(session, PanelObservation, rows, key=["source", "country", "variable", "year"])


def sync_jst(session: Session, provider: MacrohistoryProvider) -> int:
    with tracked(session, "macrohistory", "jst", "R6") as result:
        session.commit()  # fetch-then-load
        payload = provider.fetch_jst()
        result["rows"] = load(session, payload)
    return result["rows"]


def panel(session: Session, variables: list[str]) -> dict[tuple[str, int], dict[str, float]]:
    """(country, year) -> {variable: value} for the given variables."""
    out: dict[tuple[str, int], dict[str, float]] = {}
    for country, variable, year, value in session.execute(
        select(
            PanelObservation.country,
            PanelObservation.variable,
            PanelObservation.year,
            PanelObservation.value,
        ).where(PanelObservation.source == SOURCE, PanelObservation.variable.in_(variables))
    ):
        out.setdefault((country, year), {})[variable] = value
    return out


registry.register(
    Dataset(
        "macrohistory",
        "jst",
        lambda s, k, p, t: load(s, p),
        ("credit",),
        payload="bytes",
        snapshot=True,
    ),
)
