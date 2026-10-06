from datetime import UTC, date, datetime, timedelta

import httpx
import respx
from sqlalchemy import func, select
from test_timeseries import add_bars

from fin_intel import contracts, ingest, timeseries
from fin_intel.models import FederalObligation, SyncState
from fin_intel.providers import UsaspendingProvider
from fin_intel.rebuild import rebuild

URL = "https://api.usaspending.gov/api/v2/search/spending_by_category/naics/"


def page(results, has_next):
    return {
        "category": "naics",
        "results": [
            {"code": c, "name": n, "amount": a, "id": None, "total_outlays": None}
            for c, n, a in results
        ],
        "page_metadata": {"page": 1, "hasNext": has_next},
        "messages": [],
    }


@respx.mock
def test_month_is_loaded_page_by_page_and_rebuilds(session, raw_store):
    def answer(request):
        import json

        body = json.loads(request.content)
        assert body["filters"]["time_period"] == [
            {"start_date": "2024-02-01", "end_date": "2024-02-29"}
        ]
        if body["page"] == 1:
            return httpx.Response(200, json=page([("336414", "Guided Missiles", 9e8)], True))
        return httpx.Response(200, json=page([("336411", "Aircraft", 4e8)], False))

    respx.post(URL).mock(side_effect=answer)
    provider = UsaspendingProvider(raw_store=raw_store)
    assert ingest.sync_contracts_month(session, provider, date(2024, 2, 1)) == 2
    assert contracts.series(session, "3364") == [(date(2024, 2, 1), 1.3e9)]

    session.query(FederalObligation).delete()
    session.commit()
    with respx.mock:
        rebuild(session, raw_store, "policy")
    assert session.scalar(select(func.count()).select_from(FederalObligation)) == 2


def test_months_due_until_settled(session):
    today = date(2026, 10, 6)
    all_months = contracts.months(today)
    assert all_months[0] == date(2007, 10, 1) and all_months[-1] == date(2026, 9, 1)

    def loaded(month, on):
        session.add(
            SyncState(
                provider="usaspending",
                dataset="naics_month",
                key=f"{month:%Y-%m}",
                last_attempt=on,
                last_success=on,
            )
        )

    loaded(date(2007, 10, 1), datetime(2026, 1, 1, tzinfo=UTC))  # long settled
    loaded(date(2026, 8, 1), datetime(2026, 9, 15, tzinfo=UTC))  # loaded too soon: refetch
    session.commit()
    due = ingest.contract_months_due(session, today)
    assert date(2007, 10, 1) not in due and date(2026, 8, 1) in due
    assert len(due) == len(all_months) - 1


def test_gov_series_is_known_90_days_after_the_month(session):
    start = date(2024, 1, 1)
    add_bars(session, "SPY", start, [100.0] * 200)
    session.add(FederalObligation(month=date(2024, 1, 1), naics="336414", amount=5.0))
    session.add(FederalObligation(month=date(2024, 1, 1), naics="336411", amount=2.0))
    session.commit()
    days, series = timeseries.build(session, ["gov:3364"])
    values = dict(zip(days, series["gov:3364"], strict=True))
    known = date(2024, 1, 31) + timedelta(days=90)
    assert values[known - timedelta(days=1)] is None and values[known] == 7.0
    _, raw = timeseries.build(session, ["gov:3364"], pit=False)
    assert raw["gov:3364"][0] == 7.0  # dated by its month without point in time
