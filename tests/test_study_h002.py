import math
from datetime import date

import numpy as np

from fin_intel.studies import h002


def synthetic(n=420, lead=6, seed=3):
    """Industrial production with business cycles; copper's level anticipates it by `lead`
    months, iron's level mirrors it."""
    rng = np.random.default_rng(seed)
    cycle = np.cumsum(rng.normal(0, 0.01, n + lead)) + 0.03 * np.sin(np.arange(n + lead) / 9)
    ip = np.exp(cycle[:n] + np.arange(n) * 0.002)
    copper = np.exp(cycle[lead:] * 2 + rng.normal(0, 0.005, n))
    iron = np.exp(cycle[:n] * 1.5 + rng.normal(0, 0.005, n))
    months = [date(1990 + i // 12, i % 12 + 1, 28) for i in range(n)]
    return months, list(ip), list(copper), list(iron)


def test_finds_the_lead_and_the_coincidence():
    months, ip, copper, iron = synthetic()
    window = (date(1995, 1, 1), date(2023, 6, 30))
    lead = h002.analyze(months, copper, ip, ip, window, "growth", 6, "copper", reps=100)
    same = h002.analyze(months, iron, ip, ip, window, "growth", 6, "iron", reps=100)
    assert lead.peak_lag == 6 and lead.peak_low <= 6 <= lead.peak_high
    assert same.peak_lag == 0
    # Copper knows the next six months; iron only what industrial production already says.
    assert lead.oos_r2 > 0.2 and lead.clark_west_p < 0.01
    assert same.oos_r2 < lead.oos_r2


def test_transforms():
    assert h002.log_growth([1.0] * 12 + [math.e])[-1] == 1.0
    assert h002.acceleration([None, 0.1, 0.2, 0.3, 0.5])[-1] == 0.5 - 0.1
    days = [date(2026, 1, 30), date(2026, 2, 2), date(2026, 2, 27), date(2026, 3, 2)]
    assert h002.month_ends(days) == [0, 2, 3]
