from datetime import date, timedelta

import pytest
from test_timeseries import add_bars

from fin_intel import congress, events, insiders
from fin_intel.models import CusipMapping, InstitutionalFiler, Security

START = date(2026, 1, 1)


def bars(session):
    # SPY flat; XYZ +1% a day from day 3 on (the day after an event known on day 2).
    add_bars(session, "SPY", START, [100.0] * 40)
    add_bars(session, "XYZ", START, [10.0, 10.0, 10.0] + [10.0 * 1.01**i for i in range(1, 38)])


def purchase(owner, day, filed):
    return {
        "accession": f"acc-{owner}",
        "form_type": "4",
        "filing_date": filed,
        "issuer_cik": 99,
        "issuer_symbol": "XYZ",
        "owner_cik": owner,
        "owner_name": f"Insider {owner}",
        "relationship": "Director",
        "owner_title": None,
        "security_title": "Common",
        "trans_date": day,
        "trans_code": "P",
        "acquired_disposed": "A",
        "shares": 100.0,
        "price": 10.0,
        "shares_after": 1000.0,
        "direct_indirect": "D",
        "plan_10b5_1": False,
    }


def test_insider_cluster_dated_at_the_completing_filing(session):
    rows = [
        purchase(1, START - timedelta(days=20), START - timedelta(days=18)),
        purchase(2, START - timedelta(days=5), START - timedelta(days=3)),
        purchase(3, START, START + timedelta(days=1)),  # third insider: known on Jan 2
        purchase(4, START + timedelta(days=2), START + timedelta(days=3)),  # same cluster
    ]
    insiders.load(session, insiders._keyed(rows))
    found = events.insider_clusters(session)
    assert found == [events.Event("XYZ", date(2026, 1, 2), "3 insiders")]

    bars(session)
    result = events.study(session, found, horizons=(5, 21, 100))
    assert (result.events, result.priced) == (1, 1)
    five, month, too_long = result.horizons
    # Entered at the close of Jan 3 (day after it became public), held 5 trading days.
    assert five.mean_excess == pytest.approx(1.01**6 / 1.01 - 1)
    assert five.hit_rate == 1.0 and five.t_stat is None  # a single event has no spread
    assert month.n == 1 and too_long.n == 0


def test_congress_purchases_and_unpriced_tickers(session):
    report = {"doc_id": "d1", "chamber": "house", "name": "Some Member", "filed": START}
    trade = {
        "doc_id": "d1",
        "chamber": "house",
        "owner": "self",
        "asset_name": None,
        "asset_type": "ST",
        "trans_date": START - timedelta(days=10),
        "notified": None,
        "amount_max": None,
        "comment": None,
    }
    congress.load_report(
        session,
        report,
        congress._keyed(
            [
                trade | {"ticker": "XYZ", "trans_type": "purchase", "amount_min": 15001.0},
                trade | {"ticker": "NOPE", "trans_type": "purchase", "amount_min": 1001.0},
                trade | {"ticker": "XYZ", "trans_type": "sale", "amount_min": 1001.0},
            ]
        ),
    )
    found = events.congress_purchases(session)
    assert sorted(e.ticker for e in found) == ["NOPE", "XYZ"]
    assert [e.ticker for e in events.congress_purchases(session, min_amount=15000)] == ["XYZ"]
    bars(session)
    result = events.study(session, found, horizons=(5,))
    assert result.unpriced == ["NOPE"] and result.priced == 1


def test_new_13f_positions(session):
    session.add(InstitutionalFiler(cik=1, name="Fund"))
    security = Security(ticker="XYZ", origin="sec")
    session.add(security)
    session.flush()
    session.add(CusipMapping(cusip="123456789", security_id=security.id))
    session.add(CusipMapping(cusip="987654321", security_id=security.id))

    def position(period, cusip, filed):
        return {
            "filer_cik": 1,
            "period": period,
            "cusip": cusip,
            "put_call": "",
            "issuer_name": "XYZ",
            "title": "COM",
            "shares": 1.0,
            "share_type": "SH",
            "value": 1.0,
            "accession": f"a-{period}",
            "filed": filed,
        }

    from fin_intel.db import upsert
    from fin_intel.models import InstitutionalPosition

    upsert(
        session,
        InstitutionalPosition,
        [
            position(date(2025, 9, 30), "123456789", date(2025, 11, 14)),  # first quarter
            position(date(2025, 12, 31), "123456789", date(2026, 2, 14)),  # still held
            position(date(2025, 12, 31), "987654321", date(2026, 2, 14)),  # new
        ],
        key=["filer_cik", "period", "cusip", "put_call"],
    )
    assert events.new_positions(session) == [events.Event("XYZ", date(2026, 2, 14), "1")]
