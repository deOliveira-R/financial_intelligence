"""Facts read straight from a filing's XBRL instance, for when SEC's company facts lack them.

SEC's companyfacts API lags some filings: in 2026 it skipped the IFRS financials of
foreign filers using the 2025 IFRS taxonomy (served from an https namespace), so TSMC,
Toyota and Sony's latest 20-Fs had no figures there. Every XBRL filing ships an instance
document (EDGAR extracts one from inline XBRL as `*_htm.xml`); this reads its facts in the
shape of a companyfacts entry, so the same loader, statements and metrics apply.

Only standard taxonomies are kept (US GAAP, IFRS, DEI, SRT; company extensions don't map
to statements), and only facts without dimensions: a context with a segment or scenario
is a breakdown (by product, region, class), not the company total.
"""

import re
import xml.etree.ElementTree as ET
from datetime import date
from typing import Any

XBRLI = "{http://www.xbrl.org/2003/instance}"
_TAXONOMIES = [
    (re.compile(r"fasb\.org/us-gaap/"), "us-gaap"),
    (re.compile(r"xbrl\.ifrs\.org/taxonomy/[^/]+/ifrs-full"), "ifrs-full"),
    (re.compile(r"xbrl\.sec\.gov/dei/"), "dei"),
    (re.compile(r"fasb\.org/srt/"), "srt"),
]
PERIODIC_FORMS = ("10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A")


def _taxonomy(namespace: str) -> str | None:
    return next((name for pattern, name in _TAXONOMIES if pattern.search(namespace)), None)


def _measure(text: str | None) -> str:
    """iso4217:TWD -> TWD, xbrli:shares -> shares."""
    return (text or "").strip().split(":")[-1]


def instance_file(names: list[str]) -> str | None:
    """The instance document among a filing's files: EDGAR's extract of inline XBRL
    (`*_htm.xml`), else a standalone instance (not a linkbase or the filing summary)."""
    extracted = [n for n in names if n.endswith("_htm.xml")]
    if extracted:
        return extracted[0]
    linkbase = re.compile(r"_(cal|def|lab|pre|ref)\.xml$")
    plain = [
        n
        for n in names
        if n.endswith(".xml") and not linkbase.search(n) and n != "FilingSummary.xml"
    ]
    return plain[0] if len(plain) == 1 else None


def parse_instance(
    data: bytes, accession: str, form: str | None, filed: date | None
) -> dict[str, Any]:
    """A companyfacts-shaped payload ({"entityName", "facts": {taxonomy: {concept:
    {"units": {unit: [fact, ...]}}}}}) from an instance document."""
    root = ET.fromstring(data)
    contexts: dict[str, tuple[str | None, str]] = {}  # id -> (start, end) for plain contexts
    for c in root.iter(f"{XBRLI}context"):
        if c.find(f"{XBRLI}entity/{XBRLI}segment") is not None:
            continue
        if c.find(f"{XBRLI}scenario") is not None:
            continue
        period = c.find(f"{XBRLI}period")
        if period is None:
            continue
        instant = period.findtext(f"{XBRLI}instant")
        if instant:
            contexts[c.get("id", "")] = (None, instant.strip()[:10])
        else:
            start, end = period.findtext(f"{XBRLI}startDate"), period.findtext(f"{XBRLI}endDate")
            if start and end:
                contexts[c.get("id", "")] = (start.strip()[:10], end.strip()[:10])
    units: dict[str, str] = {}
    for u in root.iter(f"{XBRLI}unit"):
        divide = u.find(f"{XBRLI}divide")
        if divide is not None:
            num = divide.findtext(f"{XBRLI}unitNumerator/{XBRLI}measure")
            den = divide.findtext(f"{XBRLI}unitDenominator/{XBRLI}measure")
            units[u.get("id", "")] = f"{_measure(num)}/{_measure(den)}"
        else:
            units[u.get("id", "")] = _measure(u.findtext(f"{XBRLI}measure"))

    dei: dict[str, str] = {}
    facts: dict[str, dict[str, dict[str, dict[tuple, dict]]]] = {}
    for el in root:
        if not el.tag.startswith("{"):
            continue
        namespace, _, name = el.tag[1:].partition("}")
        taxonomy = _taxonomy(namespace)
        if taxonomy is None or el.get("contextRef") is None:
            continue
        text = (el.text or "").strip()
        if taxonomy == "dei" and el.get("unitRef") is None:
            dei.setdefault(name, text)
            continue
        context = contexts.get(el.get("contextRef", ""))
        unit = units.get(el.get("unitRef") or "")
        if context is None or not unit or not text:
            continue
        try:
            value = float(text)
        except ValueError:
            continue
        start, end = context
        fact = {"end": end, "val": value, "accn": accession, "form": form}
        if start:
            fact["start"] = start
        if filed:
            fact["filed"] = filed.isoformat()
        by_unit = facts.setdefault(taxonomy, {}).setdefault(name, {}).setdefault(unit, {})
        by_unit.setdefault((start, end), fact)  # the same fact can repeat across tables

    fy = dei.get("DocumentFiscalYearFocus")
    fp = dei.get("DocumentFiscalPeriodFocus")
    for concepts in facts.values():
        for by_unit in concepts.values():
            for entries in by_unit.values():
                for fact in entries.values():
                    fact["fy"] = int(fy) if fy and fy.isdigit() else None
                    fact["fp"] = fp
    return {
        "entityName": dei.get("EntityRegistrantName"),
        "facts": {
            taxonomy: {
                name: {"units": {unit: list(entries.values()) for unit, entries in by_unit.items()}}
                for name, by_unit in concepts.items()
            }
            for taxonomy, concepts in facts.items()
        },
    }
