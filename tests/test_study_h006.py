import math
from datetime import date, timedelta

import numpy as np

from fin_intel.studies import h006


def trading_days(start: date, n: int) -> list[date]:
    days, d = [], start
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def crash_path(days: list[date], seed: int = 1) -> list[float]:
    """A long rise, a year-long slide of about 45%, then a recovery: what a trend filter is
    meant to step aside from."""
    rng = np.random.default_rng(seed)
    n = len(days)
    drift = np.full(n, 0.0005)
    drift[n // 2 : n // 2 + 252] = -0.0025
    returns = drift + rng.normal(scale=0.008, size=n)
    return list(100 * np.cumprod(1 + returns))


def test_trend_rule_steps_aside_in_the_slide_and_the_mix_matches_exposure():
    days = trading_days(date(2000, 1, 3), 252 * 8)
    prices = crash_path(days)
    spread = [2.0] * len(days)  # never widening: the credit rule stays long
    tbill = [3.0] * len(days)
    trend = h006.simulate(days, prices, spread, tbill, "trend")
    credit = h006.simulate(days, prices, spread, tbill, "credit")
    window = (days[260], days[-1])
    t, c = h006.evaluate(trend, window, reps=100), h006.evaluate(credit, window, reps=100)
    assert t is not None and c is not None
    assert t["strategy"]["max_drawdown"] > t["hold"]["max_drawdown"]  # a smaller loss
    assert t["drawdown_ratio_vs_hold"]["value"] < 0.8
    assert 0 < t["exposure"] < 1 and t["switches_per_year"] > 0
    # Credit alone never switches here, so it is buy and hold with the same drawdown.
    assert c["exposure"] == 1.0 and c["strategy"]["max_drawdown"] == c["hold"]["max_drawdown"]
    # The constant mix holds the strategy's average exposure: its return sits between
    # cash and buy and hold.
    assert t["constant_mix"]["volatility"] < t["hold"]["volatility"]
    assert h006.verdict(t) in ("support", "partial", "rejection")
    view = h006.expanding_view(trend, window)
    assert view and all("next_year_hold" in row for row in view)


def test_monthly_decisions_hold_between_month_ends_and_cash_earns_the_bill():
    days = trading_days(date(2010, 1, 4), 300)
    prices = [100.0 - i * 0.2 for i in range(300)]  # falling: below any average
    spread = [2.0] * 300
    tbill = [2.52] * 300  # 1 basis point a day
    sim = h006.simulate(days, prices, spread, tbill, "trend", window=20)
    assert set(sim.position) == {0.0}
    assert all(math.isclose(r, 0.0001) for r in sim.strategy[25:])
    assert h006.monthly(days, sim.hold)[0] < 0
    assert h006.lag([1.0, 2.0]) == [None, 1.0]
    assert h006.decide("both", 1.0, 2.0, 1.0, 1.0) is False
    assert h006.decide("both", None, 2.0, 1.0, 1.0) is None


def test_choice_rule_prefers_the_largest_reduction_within_the_return_gap():
    def result(rule, dd, gap):
        spy = {"drawdown_ratio_vs_hold": {"value": dd}, "cagr_gap_vs_hold": {"value": gap}}
        return {"rule": rule, "window": 200, "frequency": "monthly", "assets": {"SPY": spy}}

    results = [result("trend", 0.6, -0.005), result("credit", 0.9, 0.0), result("both", 0.5, -0.03)]
    assert h006.choose(results)["rule"] == "trend"
    assert h006.choose([result("both", 0.5, -0.03)]) is None
