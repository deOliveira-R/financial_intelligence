from datetime import date

import httpx
import pytest
import respx
from sqlalchemy import func, select
from test_timeseries import add_bars

from fin_intel import ingest, lobbying, timeseries
from fin_intel.models import LobbyingReport
from fin_intel.providers import LdaProvider
from fin_intel.rebuild import rebuild


def filing(uuid, ftype, client, amount, codes, posted, registrant=1, year=2025, expenses=True):
    return {
        "filing_uuid": uuid,
        "filing_type": ftype,
        "filing_year": year,
        "income": None if expenses else amount,
        "expenses": amount if expenses else None,
        "dt_posted": f"{posted}T10:00:00-04:00",
        "registrant": {"id": registrant, "name": f"Registrant {registrant}"},
        "client": {"id": client, "name": f"Client {client}", "general_description": "x"},
        "lobbying_activities": [
            {
                "general_issue_code": c,
                "description": f"about {c}",
                "government_entities": [{"name": "SENATE"}],
            }
            for c in codes
        ],
    }


PAGE1 = {
    "next": "https://lda.gov/api/v1/filings/?page=2",
    "results": [
        filing("a", "Q1", 10, "100000.00", ["DEF", "BUD"], "2025-04-15"),
        filing("r", "RR", 11, None, ["DEF"], "2025-02-01"),  # a registration: no amount
    ],
}
PAGE2 = {
    "next": None,
    "results": [
        filing("b", "Q1", 12, "40000.00", ["DEF"], "2025-04-18", registrant=2, expenses=False),
        filing("a2", "1A", 10, "120000.00", ["DEF", "BUD"], "2025-06-01"),  # amends "a"
    ],
}


@respx.mock
def test_quarter_sync_spend_by_issue_and_rebuild(session, raw_store):
    def answer(request):
        return httpx.Response(200, json=PAGE1 if request.url.params["page"] == "1" else PAGE2)

    respx.get(url__startswith="https://lda.gov/api/v1/filings/").mock(side_effect=answer)
    lda = LdaProvider(raw_store=raw_store)
    assert ingest.sync_lobbying_quarter(session, lda, 2025, 1) == 3  # registration skipped
    # The amendment replaces the original: 120k split over DEF and BUD, plus 40k on DEF.
    assert lobbying.by_issue(session, "DEF") == [(2025, 1, pytest.approx(100000.0), 2)]
    assert lobbying.by_issue(session, "bud") == [(2025, 1, pytest.approx(60000.0), 1)]
    assert lobbying.by_client(session, "client 10")[0]["spend"] == 120000.0

    before = session.scalar(select(func.count()).select_from(LobbyingReport))
    with respx.mock:
        rebuild(session, raw_store, "lobbying")
    assert session.scalar(select(func.count()).select_from(LobbyingReport)) == before
    assert lobbying.by_issue(session, "DEF")[0][2] == pytest.approx(100000.0)

    add_bars(session, "SPY", date(2025, 4, 1), [500.0] * 60)
    days, series = timeseries.build(session, ["lobby:DEF", "lobby:DEF:count"])
    values = dict(zip(days, series["lobby:DEF"], strict=True))
    assert values[date(2025, 5, 14)] is None and values[date(2025, 5, 15)] == pytest.approx(1e5)
    assert series["lobby:DEF:count"][-1] == 2


def test_quarters_due():
    assert lobbying._quarter("Q3") == 3 and lobbying._quarter("2AY") == 2
    assert lobbying._quarter("RR") is None and lobbying._quarter("RA") is None
    assert lobbying.quarter_end(2025, 4) == date(2025, 12, 31)


def test_due_quarters_skip_settled_ones(session):
    from datetime import UTC, datetime

    from fin_intel.models import SyncState

    session.add(
        SyncState(
            provider="lda",
            dataset="filings",
            key="2025-Q1",
            last_attempt=datetime(2026, 1, 1, tzinfo=UTC),
            last_success=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    session.commit()
    due = ingest.lobbying_quarters_due(session, 2025, date(2026, 10, 6))
    assert (2025, 1) not in due and (2025, 2) in due and (2026, 3) in due and (2026, 4) not in due
