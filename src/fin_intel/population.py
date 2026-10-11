"""The research population: every company that filed financial statements with SEC since
2009, alive or dead.

Syncs follow today's listings, so on their own they forget the companies that went
bankrupt, were acquired or deregistered, and any historical test on them compares
survivors with survivors (docs/research.md). EDGAR's quarterly xbrl.idx lists every filing
with XBRL financial data (large filers from 2009, everyone from mid-2011): its periodic
filers are the population. Each member gets an issuer row, so fundamentals (company facts)
and events (submissions) load for it like for any listed company.
"""

import logging
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import Issuer, XbrlFiling
from fin_intel.pipeline import tracked, utc_today
from fin_intel.providers import SecProvider
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

FIRST_YEAR = 2009  # XBRL starts (large accelerated filers; everyone from mid-2011)
PERIODIC_FORMS = ("10-K", "10-Q", "20-F", "40-F", "10-KT", "10-QT")


def parse(text: str) -> list[dict]:
    """xbrl.idx: `CIK|Company Name|Form Type|Date Filed|Filename` after a dashed line."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("---")), None)
    if start is None:
        return []
    rows = {}
    for line in lines[start + 1 :]:
        parts = line.split("|")
        if len(parts) != 5 or not parts[0].isdigit():
            continue
        cik, company, form, filed, path = parts
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        rows[accession] = {
            "accession": accession,
            "cik": int(cik),
            "form": form.strip(),
            "filed": date.fromisoformat(filed),
            "company": company.strip() or None,
        }
    return list(rows.values())


def load(session: Session, payload: bytes) -> int:
    """A quarter's filings, and an issuer row for each periodic filer not known yet (named
    as in its latest filing here; company facts and submissions fill in the rest)."""
    rows = parse(payload.decode("latin-1"))
    if not rows:
        return 0
    upsert(session, XbrlFiling, rows, key=["accession"])
    names: dict[int, str | None] = {}
    for r in sorted(rows, key=lambda r: r["filed"]):
        if r["form"].removesuffix("/A") in PERIODIC_FORMS:
            names[r["cik"]] = r["company"]
    known = set(session.scalars(select(Issuer.cik).where(Issuer.cik.in_(names))))
    new = [{"cik": c, "name": n} for c, n in names.items() if c not in known]
    if new:
        upsert(session, Issuer, new, key=["cik"], update=[])
    return len(rows)


def quarters(today: date) -> list[tuple[int, int]]:
    last = (today.year, (today.month - 1) // 3 + 1)
    return [
        (y, q) for y in range(FIRST_YEAR, today.year + 1) for q in (1, 2, 3, 4) if (y, q) <= last
    ]


def sync_population(session: Session, sec_provider: SecProvider) -> int:
    """Every quarter's index once, plus the current and previous quarters again (filings
    keep arriving until a quarter ends; late ones are indexed after)."""
    with tracked(session, "sec", "xbrl_index", "all") as result:
        session.commit()  # fetch-then-load
        store = sec_provider.raw_store
        have = set(store.latest_hashes("sec", "xbrl_index")) if store else set()
        wanted = quarters(utc_today())
        recent = set(wanted[-2:])
        rows = 0
        for year, quarter in wanted:
            if f"{year}Q{quarter}" in have and (year, quarter) not in recent:
                continue
            payload = sec_provider.fetch_xbrl_index(year, quarter)
            rows += load(session, payload)
            session.commit()
        result["rows"] = rows
    return result["rows"]


def members(session: Session, since: date | None = None) -> set[int]:
    """Companies that filed a periodic report with financial data (since a date)."""
    stmt = select(XbrlFiling.cik).where(func.replace(XbrlFiling.form, "/A", "").in_(PERIODIC_FORMS))
    if since:
        stmt = stmt.where(XbrlFiling.filed >= since)
    return set(session.scalars(stmt.distinct()))


registry.register(
    Dataset("sec", "xbrl_index", lambda s, k, p, t: load(s, p), ("population",), payload="bytes"),
)
