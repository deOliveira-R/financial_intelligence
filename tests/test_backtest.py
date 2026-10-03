from datetime import date

import pytest
from test_timeseries import add_bars

from fin_intel import backtest
from fin_intel.backtest import Clause

START = date(2026, 1, 1)
PRICES = [100.0, 110.0, 99.0, 120.0, 120.0]  # "px:SPY > 105" is F T F T T


def test_parse_rule_and_binds_tighter_than_or():
    groups = backtest.parse_rule("px:SPY > px:SPY|sma:200 and fred:T10Y2Y>=0 or breadth:x < 40")
    assert groups == [
        [Clause("px:SPY", ">", "px:SPY|sma:200"), Clause("fred:T10Y2Y", ">=", "0")],
        [Clause("breadth:x", "<", "40")],
    ]
    with pytest.raises(backtest.RuleError):
        backtest.parse_rule("px:SPY|sma:200")


def test_signals_treat_missing_inputs_as_unknown():
    groups = backtest.parse_rule("a:x > 1 and b:y > 1 or c:z > 1")
    series = {"a:x": [2, 2, None, 0], "b:y": [2, 0, 2, 2], "c:z": [0, 0, 0, None]}
    # day 2: a missing, c false -> unknown; day 3: a false, c missing -> unknown
    assert backtest.signals(groups, series, 4) == [True, False, None, None]


def test_position_follows_the_previous_close(session):
    add_bars(session, "SPY", START, PRICES)
    r = backtest.run(session, "SPY", "px:SPY > 105", cost_bps=0)
    assert (r.start, r.end) == (START, date(2026, 1, 5))
    # Held only over 2→3 (-10%) and 4→5 (0%); never earns the +10% and +21% days.
    assert r.strategy.total_return == pytest.approx(-0.1)
    assert r.benchmark.total_return == pytest.approx(0.2)
    assert r.exposure == 0.5
    assert [(t.entry, t.exit, t.ret) for t in r.trade_list] == [
        (date(2026, 1, 2), date(2026, 1, 3), pytest.approx(-0.1)),
        (date(2026, 1, 4), None, pytest.approx(0.0)),
    ]
    assert r.win_rate == 0.0
    assert r.strategy.max_drawdown == pytest.approx(-0.1)


def test_costs_and_shorting(session):
    add_bars(session, "SPY", START, PRICES)
    costly = backtest.run(session, "SPY", "px:SPY > 105", cost_bps=100)
    assert costly.strategy.total_return == pytest.approx(0.89 * 0.99 * 0.99 - 1)
    flips = backtest.run(session, "SPY", "px:SPY > 105", cost_bps=0, short=True)
    assert flips.strategy.total_return == pytest.approx(0.9 * 0.9 * (1 - 21 / 99) - 1)
    assert flips.exposure == 1.0


def test_start_and_by_year(session):
    add_bars(session, "SPY", date(2025, 12, 30), [100.0, 100.0, *PRICES])
    r = backtest.run(session, "SPY", "px:SPY > 0", start=START, cost_bps=0)
    assert r.start == START
    assert [y["year"] for y in r.by_year] == [2026]
    assert r.by_year[0]["strategy"] == pytest.approx(0.2)


def test_unknown_series_is_a_rule_error(session):
    add_bars(session, "SPY", START, PRICES)
    with pytest.raises(backtest.RuleError, match="unknown ticker"):
        backtest.run(session, "SPY", "px:NOPE > 1")


def test_starts_when_every_input_has_data(session):
    from fin_intel.models import EconomicSeries, EconomicVintage

    add_bars(session, "SPY", START, PRICES)
    session.add(EconomicSeries(id="X", source="fred"))
    session.add(
        EconomicVintage(
            series_id="X", date=date(2026, 1, 3), realtime_start=date(2026, 1, 3), value=1.0
        )
    )
    session.commit()
    # "px:SPY > 105 and fred:X > 0" is decidable (False) on day 1 already, but X starts on day 3.
    r = backtest.run(session, "SPY", "px:SPY > 105 and fred:X > 0", cost_bps=0)
    assert r.start == date(2026, 1, 3)
