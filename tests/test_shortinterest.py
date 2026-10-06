import json
from datetime import date

import httpx
import respx
from sqlalchemy import func, select
from test_timeseries import add_bars

from fin_intel import ingest, shortinterest, timeseries
from fin_intel.models import Security, ShortInterest
from fin_intel.providers import FinraProvider
from fin_intel.rebuild import rebuild

URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"


def record(symbol, settled, short, dtc):
    return {
        "symbolCode": symbol,
        "settlementDate": settled,
        "currentShortPositionQuantity": short,
        "averageDailyVolumeQuantity": short / dtc,
        "daysToCoverQuantity": dtc,
        "marketClassCode": "NYSE",
    }


def finra_api(by_date):
    """A fake API: `by_date` maps settlement dates to their records."""

    def answer(request):
        body = json.loads(request.content)
        day = body["compareFilters"][0]["fieldValue"]
        records = by_date.get(day, [])
        if not records:
            return httpx.Response(204)
        offset, limit = body.get("offset", 0), body["limit"]
        return httpx.Response(
            200, json=records[offset : offset + limit], headers={"record-total": str(len(records))}
        )

    return respx.post(URL).mock(side_effect=answer)


def test_candidates_and_publication_lag():
    mid, end = shortinterest.candidates(2026, 8)
    assert mid[0] == date(2026, 8, 14)  # the 15th is a Saturday: the Friday before first
    assert end[0] == date(2026, 8, 31)
    assert shortinterest.available_on(date(2026, 9, 15)) == date(2026, 9, 25)


@respx.mock
def test_discovers_settlement_dates_loads_pages_and_rebuilds(session, raw_store, monkeypatch):
    monkeypatch.setattr(shortinterest, "FIRST", date(2026, 8, 1))
    for ticker in ("GME", "BRK-B"):
        session.add(Security(ticker=ticker, origin="sec"))
    session.commit()
    finra_api(
        {
            "2026-08-14": [
                record("GME", "2026-08-14", 5e7, 4.0),
                record("ZZZZ", "2026-08-14", 1, 1),
            ],
            "2026-08-29": [],  # month-end fell on a weekend: probe earlier days
            "2026-08-28": [
                record("GME", "2026-08-28", 6e7, 5.0),
                record("BRK.B", "2026-08-28", 9e6, 2.0),
            ],
            "2026-09-15": [record("GME", "2026-09-15", 7e7, 6.0)],
        }
    )
    finra = FinraProvider(raw_store=raw_store)
    monkeypatch.setattr(finra, "page_size", 1)  # several pages per date
    due = ingest.short_interest_due(session, finra, date(2026, 9, 26))
    # 2026-09-15 is published on 09-25; September's month-end isn't yet.
    assert due == [date(2026, 8, 14), date(2026, 8, 28), date(2026, 9, 15)]
    for d in due:
        ingest.sync_short_interest(session, finra, d)

    def count():
        return session.scalar(select(func.count()).select_from(ShortInterest))

    assert count() == 4  # unknown symbol ZZZZ dropped; BRK.B matched to BRK-B
    before = count()
    with respx.mock:
        rebuild(session, raw_store, "shorts")
    assert count() == before


def test_si_series_is_point_in_time(session):
    add_bars(session, "GME", date(2026, 9, 1), [20.0] * 40)
    add_bars(session, "SPY", date(2026, 9, 1), [500.0] * 40)
    gme = session.query(Security).filter_by(ticker="GME").one()
    session.add(
        ShortInterest(
            security_id=gme.id,
            settlement_date=date(2026, 9, 15),
            short_position=7e7,
            days_to_cover=6.0,
        )
    )
    session.commit()
    days, series = timeseries.build(session, ["si:GME:dtc"])
    values = dict(zip(days, series["si:GME:dtc"], strict=True))
    assert values[date(2026, 9, 24)] is None and values[date(2026, 9, 25)] == 6.0
