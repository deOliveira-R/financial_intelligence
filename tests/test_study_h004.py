from datetime import date

import numpy as np

from fin_intel.studies import h004


def test_sector_forecasts_find_a_real_signal_and_verdicts():
    rng = np.random.default_rng(7)
    months = [date(1999 + i // 12, i % 12 + 1, 28) for i in range(300)]
    signal = list(rng.normal(size=300))
    steps = [0.0] * 300
    for t in range(300 - h004.HORIZON):
        steps[t + h004.HORIZON] = 0.5 * signal[t] / h004.HORIZON  # the signal leads
    noise = rng.normal(scale=0.3, size=300)
    relative = list(np.cumsum(np.asarray(steps) + noise))
    window = (date(2010, 1, 1), date(2022, 12, 31))
    real = h004.sector_forecasts(months, signal, relative, window)
    assert real["oos_r2"] > 0 and h004.verdict(real) == "significant gain"
    unrelated = h004.sector_forecasts(months, list(rng.normal(size=300)), relative, window)
    assert h004.verdict(unrelated) in ("no gain", "gain, not significant")
    skipped = h004.sector_forecasts(months, signal, relative, window, skip=h004.PANDEMIC)
    assert skipped["forecasts"] < real["forecasts"]
    assert h004.verdict({"oos_r2": None}) == "too few forecasts"
