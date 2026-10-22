from datetime import date

import numpy as np
from test_study_h006 import crash_path, trading_days

from fin_intel.studies import h009


def test_weights_scale_with_volatility_and_cap_at_one():
    assert h009.weight(0.24) == 0.5
    assert h009.weight(0.06) == 1.0
    assert h009.weight(None) is None
    prices = [100.0 * (1.01 ** (i % 2)) for i in range(30)]  # about 16% annualized
    vol = h009.realized_vol(prices, 29, 21)
    assert vol is not None and 0.15 < vol < 0.17
    assert h009.realized_vol(prices, 5, 21) is None


def test_exposure_falls_in_the_turbulent_year_and_the_mix_matches_exposure():
    days = trading_days(date(2000, 1, 3), 252 * 8)
    prices = crash_path(days)
    tbill = [3.0] * len(days)
    sim = h009.simulate(days, prices, tbill)
    window = (days[260], days[-1])
    result = h009.evaluate(sim, window)
    assert result is not None
    assert 0.3 < result["exposure"] < 1.0
    assert all(0.0 <= p <= 1.0 for p in sim.position)
    # The path's noise is constant, so the weight hovers near its cap and is rebalanced
    # by more than a point most months.
    assert result["switches_per_year"] > 6
    assert result["strategy"]["volatility"] < result["hold"]["volatility"]
    assert "sharpe_diff_vs_mix" in result and result["verdict"] in (
        "support",
        "partial",
        "rejection",
    )


def test_high_volatility_months_are_cut():
    rng = np.random.default_rng(2)
    days = trading_days(date(2010, 1, 4), 400)
    calm = list(100 * np.cumprod(1 + rng.normal(0.0003, 0.004, 200)))
    wild = list(calm[-1] * np.cumprod(1 + rng.normal(0.0, 0.03, 200)))
    sim = h009.simulate(days, calm + wild, [1.0] * 400)
    early = [p for d, p in zip(sim.days[1:], sim.position, strict=True) if d < days[200]]
    late = [p for d, p in zip(sim.days[1:], sim.position, strict=True) if d >= days[230]]
    assert np.mean(early[30:]) > 0.9 and np.mean(late) < 0.4
