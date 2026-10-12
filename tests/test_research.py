from datetime import date

import numpy as np
import pytest

from fin_intel import research


def test_holdout_opens_once_for_a_frozen_variant(session):
    variant = {"signals": ["late_filing", "auditor_change"], "alert_rate": 0.02}
    research.log(session, "H001", variant, "design", {"lift": 12.0})
    research.log(session, "H001", {**variant, "alert_rate": 0.05}, "design", {"lift": 8.0})
    assert research.trials(session, "H001") == 2
    with pytest.raises(research.HoldoutError):
        research.log(session, "H001", variant, "holdout", {})  # not frozen yet
    research.freeze(session, "H001", variant, "best lift at a 2% alert rate")
    with pytest.raises(research.HoldoutError):
        research.freeze(session, "H001", variant, "twice")
    with pytest.raises(research.HoldoutError):
        research.log(session, "H001", {**variant, "alert_rate": 0.05}, "holdout", {})
    research.log(session, "H001", variant, "holdout", {"lift": 9.0})
    with pytest.raises(research.HoldoutError):
        research.log(session, "H001", variant, "holdout", {"lift": 9.5})  # used once
    with pytest.raises(research.HoldoutError):
        research.check_period("design", date(2024, 1, 1))
    research.check_period("design", date(2023, 6, 30))
    assert research.bonferroni_t(1) == pytest.approx(1.96, abs=0.01)
    assert research.bonferroni_t(20) > 3


def test_newey_west_widens_errors_for_overlapping_returns():
    rng = np.random.default_rng(1)
    daily = rng.normal(0, 1, 3000)
    overlapping = np.convolve(daily, np.ones(20), "valid")  # 20-day sums, overlapping
    naive_se = overlapping.std(ddof=1) / np.sqrt(len(overlapping))
    hac = research.newey_west_mean(overlapping.tolist(), lags=20)
    assert hac.se > 3 * naive_se


def test_lead_lag_forecast_and_classification_tools():
    rng = np.random.default_rng(2)
    x = rng.normal(0, 1, 400).tolist()
    y = [None] * 6 + x[:-6]  # y at t+6 is x at t: x leads y by 6
    corr = research.cross_correlation(x, y, range(-12, 13))
    assert max(corr, key=corr.get) == 6

    peak = research.block_bootstrap(
        list(zip(x, y, strict=True)),
        lambda pairs: max(
            (c := research.cross_correlation(*zip(*pairs, strict=True), range(-12, 13))),
            key=c.get,
        ),
        block=24,
        reps=200,
    )
    assert peak.low <= 6 <= peak.high

    actual = rng.normal(0, 1, 300)
    signal = actual + rng.normal(0, 1, 300)
    base = np.zeros(300)
    model = signal / 2
    t, p = research.clark_west(actual, base, model)
    assert p < 0.01 and research.out_of_sample_r2(actual, base, model) > 0

    labels = [True] * 10 + [False] * 90
    assert research.auc([1.0] * 10 + [0.0] * 90, labels) == 1.0
    assert research.auc([0.5] * 100, labels) == 0.5
    flagged = [True] * 5 + [False] * 5 + [True] * 5 + [False] * 85
    assert research.lift(flagged, labels) == pytest.approx(0.5 / 0.1)

    by_company = {c: [c % 10 == 0] * 4 for c in range(100)}
    share = research.cluster_bootstrap(by_company, lambda xs: sum(xs) / len(xs), reps=300)
    assert share.low < 0.1 < share.high
