from datetime import date

import respx
from conftest import mock_fred
from sqlalchemy import select

from fin_intel import ingest, releases
from fin_intel.models import EconomicReleaseDate, EconomicSeries
from fin_intel.providers import FedProvider, FredProvider
from fin_intel.rebuild import rebuild

FRED = "https://api.stlouisfed.org/fred"


def meeting(month, days):
    return (
        f'<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5">'
        f'<strong>{month}</strong></div><div class="fomc-meeting__date col-xs-4">{days}</div>'
        "<div>Statement</div></div>"
    )


FOMC_PAGE = (
    "<h4><a>2026 FOMC Meetings</a></h4>"
    + meeting("September", "15-16*")
    + meeting("Oct/Nov", "31-1")
    + meeting("November", "22 (notation vote)")
    + "<h4><a>2025 FOMC Meetings</a></h4>"
    + meeting("December", "9-10*")
).encode()


def test_parse_fomc_uses_each_meetings_last_day():
    assert releases.parse_fomc(FOMC_PAGE) == [
        date(2025, 12, 10),
        date(2026, 9, 16),
        date(2026, 11, 1),  # a meeting spanning two months
    ]


def dates(release_id, *days):
    return {"release_dates": [{"release_id": release_id, "date": d} for d in days]}


@respx.mock
def test_sync_calendar_then_rebuild(session, raw_store):
    mock_fred()
    fred = FredProvider(raw_store=raw_store)
    ingest.sync_economic(session, fred, "UNRATE")
    respx.get(f"{FRED}/series/release").respond(
        json={"releases": [{"id": 50, "name": "Employment Situation", "link": "http://bls"}]}
    )
    respx.get(f"{FRED}/release/dates", params={"release_id": "50"}).respond(
        json=dates(50, "2026-09-04", "2026-10-02", "2026-11-06")
    )
    respx.get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm").respond(
        content=FOMC_PAGE
    )
    fed = FedProvider(raw_store=raw_store)
    assert ingest.sync_release_calendar(session, fred, fed) == 3
    assert session.get(EconomicSeries, "UNRATE").release_id == 50

    events = releases.upcoming(session, days=40, start=date(2026, 10, 1))
    assert [(e.date, e.release, e.series) for e in events] == [
        (date(2026, 10, 2), "Employment Situation", ["UNRATE"]),
        (date(2026, 11, 1), "FOMC rate decision", []),
        (date(2026, 11, 6), "Employment Situation", ["UNRATE"]),
    ]

    rebuild(session, raw_store, "economic")
    assert session.get(EconomicSeries, "UNRATE").release_id == 50
    assert len(releases.upcoming(session, days=40, start=date(2026, 10, 1))) == 3


def test_a_moved_release_date_replaces_the_old_one(session):
    releases.load_release_dates(session, 50, dates(50, "2026-10-02", "2026-11-06"))
    releases.load_release_dates(session, 50, dates(50, "2026-10-02", "2026-11-13"))
    assert session.scalars(select(EconomicReleaseDate.date)).all() == [
        date(2026, 10, 2),
        date(2026, 11, 13),
    ]


def test_daily_releases_are_hidden_by_default(session):
    from fin_intel.models import EconomicSeries

    releases.load_release_dates(session, 18, dates(18, "2026-10-05"))  # H.15
    session.add(EconomicSeries(id="DGS10", source="fred", frequency="D", release_id=18))
    session.flush()
    assert releases.upcoming(session, 10, date(2026, 10, 1)) == []
    assert len(releases.upcoming(session, 10, date(2026, 10, 1), daily=True)) == 1
