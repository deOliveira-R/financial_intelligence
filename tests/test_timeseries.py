from datetime import date, timedelta

import pytest

from fin_intel import indicators, timeseries
from fin_intel.models import (
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    EconomicVintage,
    Security,
)

D = date.fromisoformat


# --- indicators ------------------------------------------------------------------------


def test_sma_and_missing_values():
    assert indicators.sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert indicators.sma([1, None, 3, 4], 2) == [None, None, None, 3.5]


def test_ema_seeds_with_sma():
    out = indicators.ema([1.0, 2.0, 3.0, 4.0], 3)
    assert out[:2] == [None, None] and out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(0.5 * 4 + 0.5 * 2.0)


def test_rsi_extremes_and_wilder_smoothing():
    assert indicators.rsi([float(i) for i in range(20)], 14)[-1] == 100.0
    falling = indicators.rsi([float(20 - i) for i in range(20)], 14)
    assert falling[-1] == pytest.approx(0.0)
    assert indicators.rsi([1.0] * 5, 14) == [None] * 5


def test_returns_volatility_zscore_drawdown():
    assert indicators.ret([100, 110, 99], 1) == [None, pytest.approx(0.1), pytest.approx(-0.1)]
    assert indicators.diff([1, 4, 9], 2) == [None, None, 8]
    assert indicators.volatility([100.0] * 30, 21)[-1] == 0.0
    z = indicators.zscore([1.0, 2.0, 3.0, 10.0], 4)
    assert z[-1] == pytest.approx((10 - 4) / indicators._stdev([1.0, 2.0, 3.0, 10.0]))
    assert indicators.drawdown([10, 12, 9], 3) == [None, None, pytest.approx(-0.25)]


def test_yoy_uses_the_calendar():
    days = [D("2025-01-02"), D("2025-07-01"), D("2026-01-02"), D("2026-01-05")]
    assert indicators.yoy([100, 105, 110, 111], days) == [
        None,
        None,
        pytest.approx(0.1),
        pytest.approx(0.11),
    ]


def test_transforms_are_causal():
    """Changing a future value never changes earlier outputs."""
    base = [100 + (i % 7) * 1.5 + i * 0.1 for i in range(300)]
    days = [D("2025-01-01") + timedelta(days=i) for i in range(300)]
    bumped = base[:250] + [x * 3 for x in base[250:]]
    for name, (fn, windowed, default) in indicators.TRANSFORMS.items():
        args = (days,) if name == "yoy" else ((default or 20,) if windowed else ())
        before, after = fn(base, *args), fn(bumped, *args)
        assert before[:250] == after[:250], name


# --- spec parsing ----------------------------------------------------------------------


def test_parse_specs():
    spec = timeseries.parse("px:cper/px:gld|z:252")
    assert spec.terms == (("px", "CPER"), ("px", "GLD")) and spec.operator == "/"
    assert spec.transforms == (("z", 252),)
    # A dash inside a ticker isn't an operator; one before a source prefix is.
    assert timeseries.parse("px:BRK-B").terms == (("px", "BRK-B"),)
    diff = timeseries.parse("fred:DGS10-fred:DGS2")
    assert diff.operator == "-" and diff.terms[1] == ("fred", "DGS2")
    assert timeseries.parse("px:SPY|rsi").transforms == (("rsi", 14),)


@pytest.mark.parametrize("bad", ["SPY", "foo:SPY", "px:SPY|nope:3", "px:SPY|sma", "px:A/px:B/px:C"])
def test_bad_specs(bad):
    with pytest.raises(timeseries.SpecError):
        timeseries.parse(bad)


# --- engine on a database ----------------------------------------------------------------


def add_bars(session, ticker, start, closes, source="massive"):
    security = session.query(Security).filter_by(ticker=ticker).one_or_none()
    if security is None:
        security = Security(ticker=ticker, origin="massive")
        session.add(security)
        session.flush()
    session.add_all(
        DailyBar(
            security_id=security.id,
            date=start + timedelta(days=i),
            source=source,
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1000,
        )
        for i, c in enumerate(closes)
        if c is not None
    )
    session.commit()
    return security


def test_ratio_and_transforms_on_the_calendar(session):
    start = D("2026-01-01")
    add_bars(session, "SPY", start, [100, 102, 104, 106])
    add_bars(session, "GLD", start, [50, 50, None, 53])  # missing a day
    dates, series = timeseries.build(session, ["px:SPY/px:GLD", "px:SPY|ret:1"])
    assert dates == [start + timedelta(days=i) for i in range(4)]
    assert series["px:SPY/px:GLD"] == [2.0, pytest.approx(2.04), None, 2.0]
    assert series["px:SPY|ret:1"][1] == pytest.approx(0.02)
    # `start` cuts the output but transforms still see the earlier history.
    dates, series = timeseries.build(session, ["px:SPY|ret:1"], start=start + timedelta(days=1))
    assert series["px:SPY|ret:1"][0] == pytest.approx(0.02)


def test_sources_are_merged_before_adjusting(session):
    start = D("2026-03-02")
    # Deep source stops a day early; the other fills the last day. A 2:1 split on day 3.
    add_bars(session, "SPY", start, [200, 202, 101], source="tiingo")
    security = add_bars(session, "SPY", start + timedelta(days=2), [101, 103], source="massive")
    session.add(
        CorporateAction(
            security_id=security.id,
            ex_date=start + timedelta(days=2),
            action="split",
            source="massive",
            value=2.0,
        )
    )
    session.commit()
    _, series = timeseries.build(session, ["px:SPY", "close:SPY"])
    assert series["close:SPY"] == [200, 202, 101, 103]
    assert series["px:SPY"] == [100, 101, 101, 103]  # split applied once, across sources


def test_fred_point_in_time_vs_revised(session):
    start = D("2026-07-30")
    add_bars(session, "SPY", start, [100.0] * 45)  # trading days Jul 30 .. Sep 12
    session.add(EconomicSeries(id="UNRATE", source="fred"))
    session.commit()  # before its rows: foreign keys are enforced
    session.add_all(
        [
            # July: 4.1 published Aug 1, revised to 4.2 on Sep 5 (when August, 4.3, came out).
            EconomicVintage(
                series_id="UNRATE", date=D("2026-07-01"), realtime_start=D("2026-08-01"), value=4.1
            ),
            EconomicVintage(
                series_id="UNRATE", date=D("2026-07-01"), realtime_start=D("2026-09-05"), value=4.2
            ),
            EconomicVintage(
                series_id="UNRATE", date=D("2026-08-01"), realtime_start=D("2026-09-05"), value=4.3
            ),
            EconomicObservation(series_id="UNRATE", date=D("2026-07-01"), value=4.2),
            EconomicObservation(series_id="UNRATE", date=D("2026-08-01"), value=4.3),
        ]
    )
    session.commit()
    dates, pit = timeseries.build(session, ["fred:UNRATE"])
    by_date = dict(zip(dates, pit["fred:UNRATE"], strict=True))
    assert by_date[D("2026-07-31")] is None  # nothing published yet
    assert by_date[D("2026-08-01")] == 4.1  # first print
    assert by_date[D("2026-09-04")] == 4.1  # August not out yet
    assert by_date[D("2026-09-05")] == 4.3  # August released (July revised underneath)

    _, revised = timeseries.build(session, ["fred:UNRATE"], pit=False)
    by_date = dict(zip(dates, revised["fred:UNRATE"], strict=True))
    assert by_date[D("2026-07-31")] == 4.2  # look-ahead: revised July, from its obs date
    assert by_date[D("2026-08-01")] == 4.3


def test_unknown_series_errors(session):
    add_bars(session, "SPY", D("2026-01-01"), [1.0])
    with pytest.raises(timeseries.SpecError, match="unknown ticker"):
        timeseries.build(session, ["px:NOPE"])
    with pytest.raises(timeseries.SpecError, match="revision history"):
        timeseries.build(session, ["fred:NOPE"])


def test_series_starts_after_an_unexplained_jump(session):
    start = D("2026-01-01")
    add_bars(session, "SPY", start, [100.0] * 20)
    # A relisting on the old company's history: $0.25 to $40 overnight, and it holds.
    add_bars(session, "WW", start, [0.3, 0.28, 0.27, 0.26, 0.25, 0.25] + [40.0] * 14)
    dates, series = timeseries.build(session, ["px:WW"])
    assert series["px:WW"][:6] == [None] * 6 and series["px:WW"][6] == 40.0
    # The same jump on a split's ex-date is the split, already adjusted for.
    gold = add_bars(session, "GLDX", start, [10.0] * 6 + [50.0] * 14)
    session.add(
        CorporateAction(
            security_id=gold.id,
            ex_date=start + timedelta(days=6),
            action="split",
            value=0.2,
            source="massive",
        )
    )
    session.commit()
    _, series = timeseries.build(session, ["px:GLDX"])
    assert series["px:GLDX"][0] == pytest.approx(50.0)  # adjusted, not cut
