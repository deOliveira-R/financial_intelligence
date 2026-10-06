"""Members of Congress: terms, current committees, and who filed each trading report.

Periodic transaction reports name the filer but carry no identifier. House reports give
the state and district (WV03), which with the filing date identifies the member exactly;
senators are matched by name among the senators serving then, and only when exactly one
fits. Final reports can come weeks after a term ends, so terms count FILING_GRACE beyond
their end. Unmatched reports stay unlinked.

Committee assignments are the current roster only (the source keeps no history), so
using them on past trades is a look-ahead; fine for live monitoring.
"""

import re
import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import (
    Committee,
    CommitteeMembership,
    CongressReport,
    CongressReportMember,
    LegislatorTerm,
)

FILING_GRACE = timedelta(days=120)
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "md", "phd", "hon"}


def load_legislators(session: Session, payload: list[dict[str, Any]]) -> int:
    rows = []
    for person in payload or []:
        bioguide = (person.get("id") or {}).get("bioguide")
        name = person.get("name") or {}
        for term in person.get("terms") or []:
            if not bioguide or term.get("type") not in ("rep", "sen"):
                continue
            rows.append(
                {
                    "bioguide": bioguide,
                    "start": date.fromisoformat(term["start"]),
                    "end": date.fromisoformat(term["end"]),
                    "chamber": "house" if term["type"] == "rep" else "senate",
                    "state": term.get("state"),
                    "district": term.get("district"),
                    "party": term.get("party"),
                    "first": name.get("first"),
                    "last": name.get("last"),
                    "nickname": name.get("nickname"),
                    "full_name": name.get("official_full"),
                }
            )
    return upsert(session, LegislatorTerm, rows, key=["bioguide", "start"])


def load_committees(session: Session, payload: list[dict[str, Any]]) -> int:
    rows = []
    for c in payload or []:
        rows.append(
            {
                "id": c["thomas_id"],
                "name": c["name"],
                "chamber": c.get("type"),
                "parent": None,
                "jurisdiction": c.get("jurisdiction"),
            }
        )
        for sub in c.get("subcommittees") or []:
            rows.append(
                {
                    "id": c["thomas_id"] + sub["thomas_id"],
                    "name": f"{c['name']}: {sub['name']}",
                    "chamber": c.get("type"),
                    "parent": c["thomas_id"],
                    "jurisdiction": None,
                }
            )
    session.execute(delete(Committee))
    return upsert(session, Committee, rows, key=["id"])


def load_memberships(session: Session, payload: dict[str, list[dict[str, Any]]]) -> int:
    rows = [
        {
            "committee": committee,
            "bioguide": m["bioguide"],
            "title": m.get("title"),
            "rank": m.get("rank"),
            "side": m.get("party"),
        }
        for committee, members in (payload or {}).items()
        for m in members
        if m.get("bioguide")
    ]
    session.execute(delete(CommitteeMembership))
    return upsert(session, CommitteeMembership, rows, key=["committee", "bioguide"])


def _words(name: str | None) -> list[str]:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z]+", text.lower())
    return [w for w in words if w not in _SUFFIXES and len(w) > 1]


def _serving(term: LegislatorTerm, day: date) -> bool:
    return term.start <= day <= term.end + FILING_GRACE


def link(session: Session) -> int:
    """Recompute which member filed each report; returns reports linked."""
    terms = list(session.scalars(select(LegislatorTerm)))
    by_seat: dict[tuple[str, int], list[LegislatorTerm]] = defaultdict(list)
    by_chamber: dict[str, list[LegislatorTerm]] = defaultdict(list)
    for t in terms:
        if t.chamber == "house" and t.district is not None:
            by_seat[(t.state, t.district)].append(t)
        by_chamber[t.chamber].append(t)
    rows = []
    for report in session.scalars(select(CongressReport)):
        day = report.filed or (date(report.year, 7, 1) if report.year else None)
        if day is None:
            continue
        match, method = None, ""
        seat = re.fullmatch(r"([A-Z]{2})(\d{1,2}|AL)", (report.state or "").strip().upper())
        if report.chamber == "house" and seat:
            district = 0 if seat.group(2) == "AL" else int(seat.group(2))
            found = {t.bioguide: t for t in by_seat[(seat.group(1), district)] if _serving(t, day)}
            if len(found) == 1:
                match, method = next(iter(found.values())), "district"
            elif len(found) > 1:  # a seat changed hands around the filing: use the name
                words = set(_words(report.name))
                named = [t for t in found.values() if t.last and set(_words(t.last)) <= words]
                if len(named) == 1:
                    match, method = named[0], "district"
        if match is None:
            match = _by_name(
                report.name, [t for t in by_chamber[report.chamber] if _serving(t, day)]
            )
            method = "name"
        if match is not None:
            rows.append(
                {
                    "doc_id": report.doc_id,
                    "bioguide": match.bioguide,
                    "party": match.party,
                    "state": match.state,
                    "method": method,
                }
            )
    session.execute(delete(CongressReportMember))
    return upsert(session, CongressReportMember, rows, key=["doc_id"])


def _by_name(name: str | None, candidates: list[LegislatorTerm]) -> LegislatorTerm | None:
    """The one serving member whose last name (and, if needed, first name or nickname) is
    in the filer's name."""
    words = _words(name)
    if not words:
        return None
    people: dict[str, LegislatorTerm] = {}
    for t in candidates:
        last = _words(t.last)
        if last and all(w in words for w in last):
            people.setdefault(t.bioguide, t)
    if len(people) > 1:  # several share the last name: the other words must name one
        people = {
            b: t
            for b, t in people.items()
            if (set(words) - set(_words(t.last)))
            & {w for n in (t.first, t.nickname) for w in _words(n)}
        }
    return next(iter(people.values())) if len(people) == 1 else None
