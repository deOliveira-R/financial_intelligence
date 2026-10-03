from datetime import date, timedelta

import pytest

from fin_intel import breadth, timeseries
from fin_intel.models import CorporateAction, DailyBar, MarketBreadth, Security

START = date(2025, 1, 1)


def stock(session, ticker, closes, mic="XNAS", security_type="CS", volume=100):
    security = Security(ticker=ticker, origin="massive", security_type=security_type, mic=mic)
    session.add(security)
    session.flush()
    session.add_all(
        DailyBar(
            security_id=security.id,
            date=START + timedelta(days=i),
            source="massive",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=volume,
        )
        for i, c in enumerate(closes)
        if c is not None
    )
    session.commit()
    return security


def rows(session):
    return {r.date: r for r in session.query(MarketBreadth).order_by(MarketBreadth.date)}


def test_daily_counts_and_ad_line(session):
    stock(session, "UP", [10, 11, 12])
    stock(session, "DOWN", [10, 9, 9])
    stock(session, "OTC", [10, 20, 30], mic="OTC Link")  # outside the universe
    stock(session, "ETF", [10, 20, 30], security_type="ETF")  # outside the universe
    assert breadth.compute(session) == 3
    day1, day2 = rows(session)[START + timedelta(days=1)], rows(session)[START + timedelta(days=2)]
    assert (day1.count, day1.advancers, day1.decliners, day1.ad_line) == (2, 1, 1, 0)
    assert (day2.advancers, day2.unchanged, day2.ad_line) == (1, 1, 1)
    assert (day1.up_volume, day1.down_volume) == (100, 100)


def test_splits_are_not_declines_even_without_a_bar_on_the_ex_date(session):
    split = stock(session, "SPLT", [100, 102, None, 52])  # 2:1 on day 2, no bar that day
    session.add_all(
        [
            CorporateAction(
                security_id=split.id,
                ex_date=START + timedelta(days=2),
                action="split",
                source="massive",
                value=2.0,
            ),
            # Announced for the future: must not adjust anything yet.
            CorporateAction(
                security_id=split.id,
                ex_date=START + timedelta(days=30),
                action="split",
                source="massive",
                value=10.0,
            ),
        ]
    )
    session.commit()
    breadth.compute(session)
    last = rows(session)[START + timedelta(days=3)]
    assert (last.advancers, last.decliners) == (1, 0)  # 102 -> 52 is +2% after the split


def test_moving_averages_and_new_highs(session):
    closes = [100.0 + i for i in range(260)]  # a steady climb
    stock(session, "CLIMB", closes)
    breadth.compute(session)
    last = rows(session)[START + timedelta(days=259)]
    assert (last.above_50d, last.eligible_50d, last.above_200d, last.eligible_200d) == (1, 1, 1, 1)
    assert (last.new_highs, last.new_lows, last.eligible_252) == (1, 0, 1)
    assert rows(session)[START + timedelta(days=100)].eligible_200d == 0  # not enough history


def test_breadth_in_timeseries(session):
    stock(session, "UP", [10, 11, 12])
    stock(session, "DOWN", [10, 9, 8])
    spy = Security(ticker="SPY", origin="massive", security_type="ETF", mic="ARCX")
    session.add(spy)
    session.flush()
    session.add_all(
        DailyBar(security_id=spy.id, date=START + timedelta(days=i), source="massive", close=1.0)
        for i in range(3)
    )
    session.commit()
    breadth.compute(session)
    _, series = timeseries.build(session, ["breadth:ad_ratio", "breadth:NET_ADVANCES"])
    assert series["breadth:ad_ratio"] == [None, 1.0, 1.0]
    assert series["breadth:NET_ADVANCES"] == [0, 0, 0] or series["breadth:NET_ADVANCES"][1:] == [
        0,
        0,
    ]
    with pytest.raises(timeseries.SpecError, match="unknown breadth field"):
        timeseries.build(session, ["breadth:nope"])
