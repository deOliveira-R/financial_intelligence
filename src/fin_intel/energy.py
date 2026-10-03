"""EIA weekly energy data: petroleum inventories, production, refining and demand, and
natural gas storage. Stored as economic series (source "eia") under their EIA IDs.

The Weekly Petroleum Status Report covers the week ending Friday and is published the next
Wednesday at 10:30 ET; natural gas storage, the next Thursday. Inventory changes against
expectations move crude, products and energy stocks; Cushing (the WTI delivery point)
matters for the front of the futures curve.
"""

from datetime import date, timedelta
from typing import Any

# EIA ID -> (route, alias, publication lag in days after the period's Friday)
SERIES: dict[str, tuple[str, str, int]] = {
    "WCESTUS1": ("PET", "crude_stocks", 5),  # commercial crude, excluding SPR (kb)
    "WCSSTUS1": ("PET", "spr_stocks", 5),  # Strategic Petroleum Reserve (kb)
    "W_EPC0_SAX_YCUOK_MBBL": ("PET", "cushing_stocks", 5),  # Cushing, OK (kb)
    "WGTSTUS1": ("PET", "gasoline_stocks", 5),  # total motor gasoline (kb)
    "WDISTUS1": ("PET", "distillate_stocks", 5),  # distillate fuel oil (kb)
    "WCRFPUS2": ("PET", "crude_production", 5),  # field production (kb/d)
    "WCRIMUS2": ("PET", "crude_imports", 5),  # kb/d
    "WCREXUS2": ("PET", "crude_exports", 5),  # kb/d
    "WPULEUS3": ("PET", "refinery_utilization", 5),  # % of operable capacity
    "WRPUPUS2": ("PET", "products_supplied", 5),  # total demand proxy (kb/d)
    "WGFUPUS2": ("PET", "gasoline_supplied", 5),  # gasoline demand proxy (kb/d)
    "WDIUPUS2": ("PET", "distillate_supplied", 5),  # kb/d
    "NW2_EPG0_SWO_R48_BCF": ("NG", "natgas_storage", 6),  # lower-48 working gas (bcf)
}
ALIASES = {alias: series_id for series_id, (_, alias, _) in SERIES.items()}


def resolve(ident: str) -> str:
    """An EIA series ID from an ID or alias (case-insensitive)."""
    upper = ident.upper()
    if upper in SERIES:
        return upper
    if ident.lower() in ALIASES:
        return ALIASES[ident.lower()]
    raise ValueError(f"unknown EIA series {ident!r}; one of {', '.join(ALIASES)}")


def available_on(series_id: str, period: date) -> date:
    return period + timedelta(days=SERIES[series_id][2])


def due(series_id: str, latest: date | None, today: date) -> bool:
    """Whether a newer week should be out: the week after `latest` ends 7 days later and is
    published `lag` days after that."""
    return latest is None or today >= available_on(series_id, latest + timedelta(days=7))


def parse(series_id: str, payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict]]:
    """(economic_series row, observations) from a seriesid response."""
    data = (payload.get("response") or {}).get("data") or []
    first = data[0] if data else {}
    series = {
        "id": series_id,
        "source": "eia",
        "title": first.get("series-description"),
        "units": first.get("units"),
        "frequency": "W",
        "seasonal_adjustment": "NSA",
        "last_updated": None,
    }
    observations = {}
    for row in data:
        value = row.get("value")
        observations[date.fromisoformat(row["period"])] = (
            float(value) if value not in (None, "") else None
        )
    return series, [
        {"series_id": series_id, "date": d, "value": v} for d, v in sorted(observations.items())
    ]
