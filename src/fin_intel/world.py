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


# Country names as SEC submissions give them (business address) -> ISO 3166 codes.
COUNTRIES = {
    "argentina": "AR",
    "australia": "AU",
    "austria": "AT",
    "belgium": "BE",
    "bermuda": "BM",
    "brazil": "BR",
    "british virgin islands": "VG",
    "canada": "CA",
    "cayman islands": "KY",
    "chile": "CL",
    "china": "CN",
    "colombia": "CO",
    "cyprus": "CY",
    "czech republic": "CZ",
    "denmark": "DK",
    "finland": "FI",
    "france": "FR",
    "germany": "DE",
    "greece": "GR",
    "hong kong": "HK",
    "hungary": "HU",
    "india": "IN",
    "indonesia": "ID",
    "ireland": "IE",
    "isle of man": "IM",
    "israel": "IL",
    "italy": "IT",
    "japan": "JP",
    "jersey": "JE",
    "guernsey": "GG",
    "korea, republic of": "KR",
    "south korea": "KR",
    "korea": "KR",
    "luxembourg": "LU",
    "malaysia": "MY",
    "malta": "MT",
    "mexico": "MX",
    "monaco": "MC",
    "netherlands": "NL",
    "new zealand": "NZ",
    "norway": "NO",
    "peru": "PE",
    "philippines": "PH",
    "poland": "PL",
    "portugal": "PT",
    "singapore": "SG",
    "south africa": "ZA",
    "spain": "ES",
    "sweden": "SE",
    "switzerland": "CH",
    "taiwan": "TW",
    "taiwan, province of china": "TW",
    "thailand": "TH",
    "turkey": "TR",
    "united arab emirates": "AE",
    "united kingdom": "GB",
    "uruguay": "UY",
    "vietnam": "VN",
}


def sec_country(submissions: dict[str, Any]) -> str | None:
    """An SEC filer's country: where it's incorporated (a US state code means the US), else
    its business address if that's abroad. Foreign filers often list a US office as their
    business address, so incorporation comes first."""
    code = (submissions.get("stateOfIncorporation") or "").strip()
    place = (submissions.get("stateOfIncorporationDescription") or "").strip().lower()
    if len(code) == 2 and code.isalpha():
        return "US"
    if place:
        if "canada" in place:
            return "CA"
        found = COUNTRIES.get(place) or COUNTRIES.get(place.split(",")[-1].strip())
        if place == "virgin islands, british":
            found = "VG"
        if found:
            return found
    address = (submissions.get("addresses") or {}).get("business") or {}
    if address.get("isForeignLocation"):
        return COUNTRIES.get((address.get("country") or "").strip().lower())
    return None


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
