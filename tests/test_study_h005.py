import math
from datetime import date, timedelta

import numpy as np

from fin_intel.studies import h005


def months(first: date, n: int) -> list[date]:
    return [h005.shift(first, i) for i in range(n)]


def test_known_yoy_uses_the_vintage_of_both_months():
    # 2020-01 first published as 110 (base 100), then revised to 121.
    rows = [
        (date(2019, 2, 15), date(2019, 1, 1), 100.0),
        (date(2020, 2, 15), date(2020, 1, 1), 110.0),
        (date(2020, 3, 15), date(2020, 1, 1), 121.0),
    ]
    days = [date(2020, 2, 1), date(2020, 2, 20), date(2020, 3, 20)]
    out = h005.known_yoy(rows, days)
    assert out[0] is None  # 2019-01 is the latest: no year-earlier value
    assert math.isclose(out[1], math.log(1.10)) and math.isclose(out[2], math.log(1.21))


def test_forecast_finds_a_planted_signal_and_not_noise():
    rng = np.random.default_rng(3)
    ms = months(date(1980, 1, 1), 400)
    x = rng.normal(size=400)
    origins = [
        h005.Origin(m, [], [float(x[i])], float(0.8 * x[i] + rng.normal(scale=0.5)),
                    ready=h005.shift(m, 3))
        for i, m in enumerate(ms)
    ]  # fmt: skip
    window = (date(1995, 1, 1), date(2010, 12, 1))
    real = h005.forecast(origins, window, lags=2)
    assert h005.passes(real, sign=1) and not h005.passes(real, sign=-1)
    noise = [h005.Origin(o.when, [], [float(rng.normal())], o.target, o.ready) for o in origins]
    assert not h005.passes(h005.forecast(noise, window, lags=2))


def test_gap_closes_through_the_yield():
    """Yields follow copper/gold with a lag, so a gap predicts the yield's move (c1)."""
    rng = np.random.default_rng(5)
    ms = months(date(1976, 1, 1), 360)
    ratio = np.cumsum(rng.normal(scale=0.05, size=360))
    yields = np.zeros(360)
    for i in range(1, 360):
        yields[i] = yields[i - 1] + 0.3 * (2 + 4 * ratio[i - 1] - yields[i - 1]) + rng.normal(
            scale=0.05
        )
    c1, c2 = h005.gap_origins(dict(zip(ms, ratio, strict=True)), dict(zip(ms, yields, strict=True)))
    window = (date(1990, 1, 1), date(2004, 12, 1))
    c1_result = h005.forecast(c1, window, zero_base=True, lags=2)
    assert h005.passes(c1_result, sign=-1)


def test_china_impulse_lead_and_whose_activity():
    rng = np.random.default_rng(11)
    quarters = [date(1985 + i // 4, 3 * (i % 4) + 3, 1) for i in range(150)]
    ends = [h005.quarter_end(q) for q in quarters]
    impulse = rng.normal(size=150)
    log_cu = np.zeros(150)
    for i in range(150):  # copper's quarterly growth follows the impulse 3 quarters later
        log_cu[i] = (log_cu[i - 1] if i else 0) + (0.1 * impulse[i - 3] if i >= 3 else 0)
        log_cu[i] += rng.normal(scale=0.01)
    copper = {}
    for q, v in zip(quarters, log_cu, strict=True):
        for k in range(3):
            copper[h005.shift(date(q.year, q.month, 1), -2 + k)] = math.exp(10 + v)
    imp = list(zip(ends, impulse.tolist(), strict=True))
    out = h005.test_a(imp, copper, (date(1990, 1, 1), date(2020, 12, 31)), reps=100)
    assert 2 <= out["a1"]["peak_lead"] <= 6 and out["a1"]["passes"]
    assert out["a2"]["forecasts"] > 24

    ms = months(date(1999, 1, 1), 168)
    world = rng.normal(size=168)
    cu = {m: float(w + rng.normal(scale=0.3)) for m, w in zip(ms, world, strict=True)}
    china = {m: float(w) for m, w in zip(ms, world, strict=True)}
    us = {m: float(rng.normal()) for m in ms}
    b1 = h005.test_b1(cu, china, us, (date(1999, 1, 1), date(2012, 12, 31)), reps=200)
    assert b1["passes"] and b1["corr_china"] > b1["corr_us"]
    assert h005.quarter_end(date(2020, 5, 1)) == date(2020, 6, 30) - timedelta(0)
