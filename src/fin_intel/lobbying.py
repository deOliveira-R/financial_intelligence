"""Lobbying: who pays to influence which policy areas, quarterly since 2008 (Senate LDA).

A policy signal at the industry level, like federal contracts and congressional trades:
spend on defense (DEF), energy (ENG), health (HCR, MMM), trade (TRD), taxes (TAX),
telecom and technology (TEC, SCI, CPI)... rises ahead of legislation that matters to an
industry. Each quarterly report (LD-2) states one amount for the client and lists the
issue areas; spend by issue splits the amount evenly across them (a convention: reports
don't break it down). Amendments supersede the original for the same registrant, client
and quarter.

Point in time: reports are due 20 days after the quarter; late filings are common, so a
quarter counts as known AVAILABLE_AFTER its end.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import LobbyingIssue, LobbyingReport

FIRST_YEAR = 2008  # quarterly reports began in 2008 (semiannual before)
AVAILABLE_AFTER = timedelta(days=45)
REPORT_TYPES = ("Q", "T", "A", "@")  # report, termination, amendment, termination amendment


def _quarter(filing_type: str) -> int | None:
    """Q1 / 1T / 1A / 1@ (and their no-activity Y variants) -> 1; None for registrations."""
    digits = [c for c in filing_type if c.isdigit()]
    if not digits or not any(t in filing_type for t in REPORT_TYPES):
        return None
    return int(digits[0])


def quarter_end(year: int, quarter: int) -> date:
    return (date(year, quarter * 3, 28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def available_on(year: int, quarter: int) -> date:
    return quarter_end(year, quarter) + AVAILABLE_AFTER


def _amount(f: dict[str, Any]) -> float | None:
    for field in ("income", "expenses"):
        if f.get(field) not in (None, ""):
            return float(f[field])
    return 0.0 if (f.get("filing_type") or "").endswith("Y") else None


def load_page(session: Session, payload: dict[str, Any]) -> int:
    reports, issues = [], []
    for f in payload.get("results") or []:
        quarter = _quarter(f.get("filing_type") or "")
        if quarter is None or not f.get("filing_uuid"):
            continue
        registrant, client = f.get("registrant") or {}, f.get("client") or {}
        posted = (f.get("dt_posted") or "")[:10]
        reports.append(
            {
                "filing_uuid": f["filing_uuid"],
                "year": f["filing_year"],
                "quarter": quarter,
                "filing_type": f["filing_type"],
                "registrant_id": registrant.get("id"),
                "registrant": (registrant.get("name") or "")[:256] or None,
                "client_id": client.get("id"),
                "client": (client.get("name") or "")[:256] or None,
                "client_description": client.get("general_description"),
                "amount": _amount(f),
                "posted": date.fromisoformat(posted) if posted else None,
            }
        )
        seen = {}
        for a in f.get("lobbying_activities") or []:
            code = a.get("general_issue_code")
            if not code:
                continue
            entities = ", ".join(g.get("name", "") for g in a.get("government_entities") or [])
            entry = seen.setdefault(code, {"description": [], "entities": set()})
            entry["description"].append((a.get("description") or "").strip())
            entry["entities"].update(e for e in entities.split(", ") if e)
        for code, entry in seen.items():
            issues.append(
                {
                    "filing_uuid": f["filing_uuid"],
                    "code": code,
                    "description": " | ".join(d for d in entry["description"] if d)[:4000] or None,
                    "entities": ", ".join(sorted(entry["entities"])) or None,
                }
            )
    uuids = [r["filing_uuid"] for r in reports]
    if uuids:
        session.execute(delete(LobbyingIssue).where(LobbyingIssue.filing_uuid.in_(uuids)))
    upsert(session, LobbyingReport, reports, key=["filing_uuid"])
    upsert(session, LobbyingIssue, issues, key=["filing_uuid", "code"])
    return len(reports)


def _current(session: Session):
    """The reports that count: the latest posted per registrant, client and quarter."""
    ranked = select(
        LobbyingReport.filing_uuid,
        LobbyingReport.year,
        LobbyingReport.quarter,
        LobbyingReport.amount,
        func.row_number()
        .over(
            partition_by=(
                LobbyingReport.registrant_id,
                LobbyingReport.client_id,
                LobbyingReport.year,
                LobbyingReport.quarter,
            ),
            order_by=(LobbyingReport.posted.desc(), LobbyingReport.filing_uuid.desc()),
        )
        .label("rank"),
    ).subquery()
    return select(ranked).where(ranked.c.rank == 1).subquery()


def by_issue(session: Session, code: str) -> list[tuple[int, int, float, int]]:
    """(year, quarter, spend, reports) for one issue code, spend split evenly across each
    report's issues."""
    current = _current(session)
    issue_count = (
        select(LobbyingIssue.filing_uuid, func.count().label("n"))
        .group_by(LobbyingIssue.filing_uuid)
        .subquery()
    )
    rows = session.execute(
        select(
            current.c.year,
            current.c.quarter,
            func.sum(func.coalesce(current.c.amount, 0.0) / issue_count.c.n),
            func.count(),
        )
        .join(LobbyingIssue, LobbyingIssue.filing_uuid == current.c.filing_uuid)
        .join(issue_count, issue_count.c.filing_uuid == current.c.filing_uuid)
        .where(LobbyingIssue.code == code.upper())
        .group_by(current.c.year, current.c.quarter)
        .order_by(current.c.year, current.c.quarter)
    )
    return [(y, q, float(s or 0.0), int(n)) for y, q, s, n in rows]


def by_client(session: Session, name: str, limit: int = 40) -> list[dict[str, Any]]:
    """Quarterly spend of clients whose name contains `name` (e.g. "lockheed")."""
    current = _current(session)
    rows = session.execute(
        select(
            LobbyingReport.client,
            current.c.year,
            current.c.quarter,
            func.sum(current.c.amount),
            func.count(),
        )
        .join(LobbyingReport, LobbyingReport.filing_uuid == current.c.filing_uuid)
        .where(LobbyingReport.client.ilike(f"%{name}%"))
        .group_by(LobbyingReport.client, current.c.year, current.c.quarter)
        .order_by(
            current.c.year.desc(), current.c.quarter.desc(), func.sum(current.c.amount).desc()
        )
        .limit(limit)
    )
    return [
        {"client": c, "year": y, "quarter": q, "spend": s, "reports": n} for c, y, q, s, n in rows
    ]
