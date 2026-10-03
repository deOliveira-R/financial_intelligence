from datetime import date

import pytest
import respx
from sqlalchemy import select
from test_timeseries import add_bars

from fin_intel import energy, ingest, timeseries
from fin_intel.models import EconomicObservation, EconomicSeries
from fin_intel.providers import EiaProvider
from fin_intel.rebuild import rebuild


def payload(*points):
    return {
        "response": {
            "total": str(len(points)),
            "frequency": "weekly",
            "data": [
                {
                    "period": period,
                    "series": "WCESTUS1",
                    "series-description": "U.S. Ending Stocks excluding SPR of Crude Oil "
                    "(Thousand Barrels)",
                    "value": value,
                    "units": "MBBL",
                }
                for period, value in points
            ],
        }
    }


def test_resolve_and_parse():
    assert energy.resolve("crude_stocks") == "WCESTUS1"
    assert energy.resolve("wcestus1") == "WCESTUS1"
    with pytest.raises(ValueError):
        energy.resolve("whale_oil")
    series, obs = energy.parse("WCESTUS1", payload(("2026-09-25", 427320), ("2026-09-18", None)))
    assert series["source"] == "eia" and series["units"] == "MBBL"
    assert obs == [  # sorted by date
        {"series_id": "WCESTUS1", "date": date(2026, 9, 18), "value": None},
        {"series_id": "WCESTUS1", "date": date(2026, 9, 25), "value": 427320.0},
    ]


@respx.mock
def test_sync_then_rebuild(session, raw_store):
    route = respx.get("https://api.eia.gov/v2/seriesid/PET.WCESTUS1.W").respond(
        200, json=payload(("2026-09-18", 430000), ("2026-09-25", 427320))
    )
    assert ingest.sync_eia(session, EiaProvider(raw_store=raw_store), "WCESTUS1") == 2
    assert route.calls.last.request.url.params["api_key"] == "DEMO_KEY"
    rebuild(session, raw_store, "economic")
    assert session.get(EconomicSeries, "WCESTUS1").source == "eia"
    values = session.scalars(select(EconomicObservation.value).order_by(EconomicObservation.date))
    assert list(values) == [430000, 427320]
    # The key never reaches the raw store.
    params = raw_store.engine.connect().exec_driver_sql("select params from raw_responses")
    assert "DEMO_KEY" not in str(params.all())


def test_timeseries_waits_for_the_wednesday_release(session):
    add_bars(session, "SPY", date(2026, 9, 21), [100.0] * 14)
    ingest.load_eia_series(
        session, "WCESTUS1", payload(("2026-09-18", 430000), ("2026-09-25", 427320))
    )
    session.commit()
    spec = "eia:crude_stocks|diff:1"
    days, pit = timeseries.build(session, ["eia:crude_stocks"])
    by_day = dict(zip(days, pit["eia:crude_stocks"], strict=True))
    assert by_day[date(2026, 9, 22)] is None  # week of the 18th published Wednesday the 23rd
    assert by_day[date(2026, 9, 23)] == 430000
    assert by_day[date(2026, 9, 29)] == 430000
    assert by_day[date(2026, 9, 30)] == 427320
    assert timeseries.parse(spec)  # transforms apply as to any series


def test_due_after_the_next_release():
    friday = date(2026, 9, 25)  # the latest week loaded
    assert not energy.due("WCESTUS1", friday, date(2026, 9, 30))  # next week ends Oct 2
    assert not energy.due("WCESTUS1", friday, date(2026, 10, 6))
    assert energy.due("WCESTUS1", friday, date(2026, 10, 7))  # Wednesday release
    assert not energy.due("NW2_EPG0_SWO_R48_BCF", friday, date(2026, 10, 7))  # Thursday
    assert energy.due("WCESTUS1", None, date(2026, 10, 1))
