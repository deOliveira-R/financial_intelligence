"""European listed companies' annual reports (ESEF, via filings.xbrl.org).

Since 2021 companies listed on EU regulated markets (and the UK) file annual reports in
inline XBRL under the ESEF rules, tagged with the IFRS taxonomy. filings.xbrl.org collects
them and publishes each as xBRL-JSON. Only IFRS facts without dimensions are kept (company
totals), as for SEC filings. In xBRL-JSON periods end at midnight of the next day
("2025-01-01T00:00:00/2026-01-01T00:00:00" is calendar 2025), so a day is taken off.
"""

import json
from datetime import date, timedelta
from typing import Any

PLAIN = {"concept", "entity", "period", "unit", "language"}


def _date(text: str) -> date:
    """A period end or instant: midnight belongs to the previous day."""
    d = date.fromisoformat(text[:10])
    return d - timedelta(days=1) if text[10:19] in ("", "T00:00:00") else d


def _unit(text: str) -> str:
    """iso4217:EUR/xbrli:shares -> EUR/shares."""
    return "/".join(part.split(":")[-1] for part in text.split("/"))


def filings(index: dict[str, Any]) -> list[dict[str, Any]]:
    """ESEF filings in an index page: LEI, entity name, country, period end, version,
    report URL and date added."""
    names = {
        e["id"]: (e.get("attributes") or {}).get("name")
        for e in index.get("included") or []
        if e.get("type") == "entity"
    }
    out = []
    for f in index.get("data") or []:
        a = f.get("attributes") or {}
        fxo = a.get("fxo_id") or ""
        if "-ESEF-" not in fxo or not a.get("json_url"):
            continue
        lei = fxo.split("-")[0]
        entity = ((f.get("relationships") or {}).get("entity") or {}).get("data") or {}
        out.append(
            {
                "lei": lei,
                "name": names.get(entity.get("id")),
                "country": a.get("country"),
                "period_end": a.get("period_end"),
                "version": int(fxo.rsplit("-", 1)[-1]) if fxo.rsplit("-", 1)[-1].isdigit() else 0,
                "json_url": a["json_url"],
                "added": (a.get("date_added") or "")[:10],
                "fxo_id": fxo,
            }
        )
    return out


def parse_report(data: bytes, accession: str, filed: date | None) -> dict[str, Any]:
    """A companyfacts-shaped payload from an xBRL-JSON report."""
    doc = json.loads(data)
    facts: dict[str, dict[str, dict[str, dict]]] = {}
    fy = None
    for f in (doc.get("facts") or {}).values():
        dims = f.get("dimensions") or {}
        concept, period, unit = dims.get("concept", ""), dims.get("period"), dims.get("unit")
        if not set(dims) <= PLAIN or not concept.startswith("ifrs-full:") or not period or not unit:
            continue
        try:
            value = float(f.get("value"))
        except TypeError, ValueError:
            continue
        start, _, end = period.partition("/")
        entry: dict[str, Any] = {"accn": accession, "form": "annual", "val": value}
        if end:  # a duration starts on its first day and ends at midnight after its last
            entry["start"] = start[:10]
            entry["end"] = _date(end).isoformat()
        else:
            entry["end"] = _date(start).isoformat()
        if filed:
            entry["filed"] = filed.isoformat()
        by_unit = facts.setdefault("ifrs-full", {}).setdefault(concept.split(":", 1)[1], {})
        by_unit.setdefault(_unit(unit), {}).setdefault((entry.get("start"), entry["end"]), entry)
        if end and (fy is None or entry["end"] > fy):
            fy = entry["end"]
    year = int(fy[:4]) if fy else None
    out: dict[str, Any] = {}
    for concept, by_unit in (facts.get("ifrs-full") or {}).items():
        units = {}
        for unit, entries in by_unit.items():
            units[unit] = [e | {"fy": year, "fp": "FY"} for e in entries.values()]
        out[concept] = {"units": units}
    return {"facts": {"ifrs-full": out} if out else {}}
