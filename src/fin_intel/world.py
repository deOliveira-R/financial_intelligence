"""Issuers beyond SEC filers: companies known from other regulators' filings.

Each gets an issuer id in a reserved range above SEC's CIKs (which stay below 10^10), so
facts, statements, metrics and screens work the same for every company. Ids are derived
from the regulator's own identifier, so they're stable across rebuilds.
"""

import hashlib
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import Issuer

SOURCES = {
    "edinet": ("JP", 1 * 10**10),  # EDINET code E01441 -> 10000001441
    "dart": ("KR", 2 * 10**10),  # DART corp code 00126380 -> 20000126380
    "twse": ("TW", 3 * 10**10),  # stock code 2330 -> 30000002330 (TWSE and TPEx share codes)
    "esef": (None, 10**12),  # LEI, hashed into 40 bits
}


def issuer_id(source: str, source_id: str) -> int:
    base = SOURCES[source][1]
    digits = "".join(c for c in source_id if c.isdigit())
    if source != "esef" and digits and len(digits) <= 9:
        return base + int(digits)
    return base + int(hashlib.sha256(source_id.encode()).hexdigest()[:10], 16)


def ensure_issuer(
    session: Session,
    source: str,
    source_id: str,
    name: str | None = None,
    country: str | None = None,
    **extra: Any,
) -> int:
    """Create or update an issuer from another regulator; returns its id. Only the fields
    given are written, so a later, sparser source doesn't erase earlier details."""
    cik = issuer_id(source, source_id)
    row = {
        "cik": cik,
        "source": source,
        "source_id": source_id,
        "country": country or SOURCES[source][0],
        **({"name": name} if name else {}),
        **{k: v for k, v in extra.items() if v is not None},
    }
    upsert(session, Issuer, [row], key=["cik"], update=[k for k in row if k != "cik"])
    return cik


def facts_payload(facts: list[dict[str, Any]], name: str | None = None) -> dict[str, Any]:
    """A companyfacts-shaped payload from flat facts: taxonomy, concept, unit, start
    (None for instants), end, value, accession, form, filed, fy, fp. The first fact per
    (concept, unit, period) wins, so callers list preferred sources first."""
    out: dict[str, dict[str, dict]] = {}
    seen = set()
    for f in facts:
        key = (f["taxonomy"], f["concept"], f["unit"], f.get("start"), f["end"])
        if key in seen:
            continue
        seen.add(key)
        entry = {
            "end": _iso(f["end"]),
            "val": f["value"],
            "accn": f["accession"],
            "form": f.get("form"),
            "filed": _iso(f.get("filed")),
            "fy": f.get("fy"),
            "fp": f.get("fp"),
        }
        if f.get("start"):
            entry["start"] = _iso(f["start"])
        concept = out.setdefault(f["taxonomy"], {}).setdefault(f["concept"], {"units": {}})
        concept["units"].setdefault(f["unit"], []).append(entry)
    return {"entityName": name, "facts": out}


def _iso(value: date | str | None) -> str | None:
    return value.isoformat() if isinstance(value, date) else value
