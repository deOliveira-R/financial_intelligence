from datetime import date, datetime

import respx
from sqlalchemy import select

from fin_intel import filing_events, ingest
from fin_intel.models import CorporateEvent, Issuer
from fin_intel.providers import SecProvider
from fin_intel.rebuild import rebuild


def table(*filings):
    """filings: (accession, form, filed, accepted, items)"""
    cols = ("accessionNumber", "form", "filingDate", "acceptanceDateTime", "items")
    return {c: [f[i] for f in filings] for i, c in enumerate(cols)} | {
        "reportDate": [f[2] for f in filings]
    }


RECENT = table(
    ("A-26-1", "8-K", "2026-07-30", "2026-07-30T20:30:28.000Z", "2.02,9.01"),
    ("A-26-2", "8-K", "2026-08-01", "2026-08-01T12:00:00.000Z", "5.02,1.01"),
    ("A-26-3", "8-K", "2026-08-02", "2026-08-02T12:00:00.000Z", "9.01"),  # exhibits only
    ("A-26-4", "4", "2026-08-03", "2026-08-03T12:00:00.000Z", ""),  # not an event form
    ("A-26-5", "SC 13D", "2026-08-04", "2026-08-04T12:00:00.000Z", ""),
)
OLD = table(("A-03-1", "8-K", "2003-04-20", "2003-04-20T21:00:00.000Z", ""))  # pre-2004: no items


def test_parse_events():
    rows = filing_events.parse(RECENT, 320193)
    assert [(r["accession"], r["item"]) for r in rows] == [
        ("A-26-1", "2.02"),
        ("A-26-2", "5.02"),
        ("A-26-2", "1.01"),
        ("A-26-5", ""),
    ]
    assert rows[0]["accepted"] == datetime(2026, 7, 30, 20, 30, 28)
    assert filing_events.parse(OLD, 1)[0]["item"] == "?"
    assert filing_events.describe("8-K", "2.02").startswith("Results of operations")
    assert filing_events.describe("SC 13D", "").startswith("Activist stake")


@respx.mock
def test_sync_loads_recent_and_older_pages_then_rebuilds(session, raw_store):
    session.add(Issuer(cik=320193, name="Apple"))
    session.commit()
    page = "CIK0000320193-submissions-001.json"
    respx.get("https://data.sec.gov/submissions/CIK0000320193.json").respond(
        json={
            "sic": "3571",
            "category": "Large accelerated filer",
            "filings": {"recent": RECENT, "files": [{"name": page}]},
        }
    )
    older = respx.get(f"https://data.sec.gov/submissions/{page}").respond(json=OLD)
    sec = SecProvider(raw_store=raw_store)
    assert ingest.sync_events(session, sec, 320193) > 0
    assert ingest.sync_events(session, sec, 320193) >= 0
    assert older.call_count == 1  # an older page is fetched once

    def events():
        return session.execute(
            select(CorporateEvent.accession, CorporateEvent.item).order_by(
                CorporateEvent.filed, CorporateEvent.item
            )
        ).all()

    before = events()
    assert before[0] == ("A-03-1", "?") and ("A-26-1", "2.02") in before
    rebuild(session, raw_store, "events")
    assert events() == before
    assert session.get(Issuer, 320193).sic == 3571
    assert session.scalar(
        select(CorporateEvent.filed).where(CorporateEvent.item == "2.02")
    ) == date(2026, 7, 30)
