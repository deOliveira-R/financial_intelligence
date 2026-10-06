"""Bills and joint resolutions in Congress since 2011 (GovInfo Bill Status): policy area,
subjects, sponsor, cosponsors, committees, progress and enactment.

Legislative activity by policy area (Armed Forces and National Security, Energy, Health,
Science Technology Communications, Foreign Trade…) sits beside lobbying spend and
congressional trades as an industry-level policy signal: what Congress is working on.

Point in time: a bill is dated by its introduction, but CRS assigns its policy area and
subjects days to weeks later, a small look-ahead for monthly counts. Enactment is dated by
the "Became Public Law" action.
"""

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import Bill, BillSubject

FIRST_CONGRESS = 112  # 2011-2012
ALIASES = {
    "defense": "Armed Forces and National Security",
    "energy": "Energy",
    "health": "Health",
    "tech": "Science, Technology, Communications",
    "trade": "Foreign Trade and International Finance",
    "finance": "Finance and Financial Sector",
    "taxation": "Taxation",
    "commerce": "Commerce",
    "environment": "Environmental Protection",
    "transportation": "Transportation and Public Works",
    "agriculture": "Agriculture and Food",
    "international": "International Affairs",
    "economy": "Economics and Public Finance",
    "labor": "Labor and Employment",
    "immigration": "Immigration",
    "water": "Water Resources Development",
    "lands": "Public Lands and Natural Resources",
}


def congress_for(day: date) -> int:
    """The congress in session on a day (each starts January 3 of an odd year)."""
    year = day.year - (1 if day.year % 2 == 0 or (day.month == 1 and day.day < 3) else 0)
    return (year - 1789) // 2 + 1


def _text(node: ET.Element | None, path: str) -> str | None:
    found = node.find(path) if node is not None else None
    return (found.text or "").strip() or None if found is not None else None


def _date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def parse(xml: bytes) -> tuple[dict[str, Any], list[str]] | None:
    """(bill row, subjects) from one Bill Status document."""
    bill = ET.fromstring(xml).find("bill")
    if bill is None:
        return None
    congress, kind, number = _text(bill, "congress"), _text(bill, "type"), _text(bill, "number")
    if not (congress and kind and number):
        return None
    sponsor = bill.find("sponsors/item")
    enacted = None
    for action in bill.findall("actions/item"):
        if re.search(r"became public law", _text(action, "text") or "", re.I):
            enacted = _date(_text(action, "actionDate"))
    laws = [n for n in (_text(i, "number") for i in bill.findall("laws/item")) if n]
    committees = sorted(
        {c for c in (_text(i, "systemCode") for i in bill.findall("committees/item")) if c}
    )
    row = {
        "id": f"{congress}-{kind.lower()}-{number}",
        "congress": int(congress),
        "type": kind.lower(),
        "number": int(number),
        "introduced": _date(_text(bill, "introducedDate")),
        "title": _text(bill, "title"),
        "policy_area": _text(bill, "policyArea/name") or _text(bill, "subjects/policyArea/name"),
        "sponsor": _text(sponsor, "bioguideId"),
        "sponsor_party": _text(sponsor, "party"),
        "cosponsors": len(bill.findall("cosponsors/item")),
        "committees": ",".join(committees) or None,
        "latest_action": _date(_text(bill, "latestAction/actionDate")),
        "latest_action_text": _text(bill, "latestAction/text"),
        "law": laws[0][:16] if laws else None,
        "enacted": enacted,
    }
    subjects = sorted(
        {
            s[:128]
            for s in (_text(i, "name") for i in bill.findall("subjects/legislativeSubjects/item"))
            if s
        }
    )
    return row, subjects


def load_zip(session: Session, body: bytes) -> int:
    rows, subjects = [], []
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        for name in archive.namelist():
            if not name.endswith(".xml"):
                continue
            parsed = parse(archive.read(name))
            if parsed is None:
                continue
            row, names = parsed
            rows.append(row)
            subjects += [{"bill_id": row["id"], "subject": s} for s in names]
    upsert(session, Bill, rows, key=["id"])
    upsert(session, BillSubject, subjects, key=["bill_id", "subject"])
    return len(rows)


def resolve(ident: str) -> str:
    """A policy area from an alias (defense, energy…) or its exact name."""
    return ALIASES.get(ident.lower(), ident)


def monthly(session: Session, area: str, enacted: bool = False) -> list[tuple[date, int]]:
    """(first of month, bills introduced — or enacted — that month) for a policy area."""
    column = Bill.enacted if enacted else Bill.introduced
    month = func.strftime("%Y-%m-01", column)
    rows = session.execute(
        select(month, func.count())
        .where(func.lower(Bill.policy_area) == resolve(area).lower(), column.is_not(None))
        .group_by(month)
        .order_by(month)
    )
    return [(date.fromisoformat(m), n) for m, n in rows]
