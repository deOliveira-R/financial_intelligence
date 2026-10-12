from datetime import date

import numpy as np

from fin_intel.studies import h003


def test_building_blocks():
    assert h003.percentiles([None] + [float(i) for i in range(25)])[-1] == 1.0
    assert h003.percentiles([1.0, 2.0])[-1] is None  # too little history
    monthly = [(date(2008, m, 1), 1.0 if 4 <= m <= 9 else 0.0) for m in range(1, 13)]
    starts, last = h003.recession_starts(monthly)
    assert starts == {date(2008, 4, 1)} and last == date(2008, 12, 1)
    assert h003.window_outcome(starts, last, date(2008, 1, 15), 6) is True
    assert h003.window_outcome(starts, last, date(2008, 1, 15), 36) is None  # not covered
    index = [
        (date(2020, m, 1), v)
        for m, v in zip(range(1, 13), [100, 90, 65, 80] + [90] * 8, strict=True)
    ]
    assert h003.drawdown_outcome(index, date(2020, 1, 15), 6) is True
    assert h003.drawdown_outcome(index, date(2020, 4, 15), 6) is False
    assert h003.yearly_impulse({1: 100.0, 2: 110.0, 3: 130.0}, {3: 1000.0}, 3) == 1.0


def synthetic_rows(seed=4):
    """30 countries x 120 quarters. A crisis follows Keen's condition (high debt, negative
    impulse) 40% of the time without a public offset, 8% with one, and 4% otherwise."""
    rng = np.random.default_rng(seed)
    rows = []
    for c in range(30):
        for q in range(120):
            row = h003.Row(
                "bis",
                f"C{c:02d}",
                date(1985 + q // 4, 3 * (q % 4) + 1, 1),
                date(1985 + q // 4, 3 * (q % 4) + 1, 1),
                debt=100.0,
                debt_pct=float(rng.uniform()),
                impulse=float(rng.normal()),
                gov_impulse=float(rng.normal()),
                credit_gap=float(rng.normal()),
            )
            p = 0.04
            if row.signal:
                p = 0.08 if row.offset else 0.40
            row.outcomes["crisis_36"] = bool(rng.uniform() < p)
            rows.append(row)
    return rows


def test_conditional_rates_logit_and_out_of_country_prediction():
    rows = synthetic_rows()
    rates = {r.group.strip(): r for r in h003.conditional_rates(rows, "crisis_36", reps=200)}
    keen = rates["high debt, negative impulse"]
    assert keen.ratio > 2 and keen.ratio_low > 1
    assert rates["without public offset"].ratio > rates["with public offset"].ratio
    fit = h003.logit(rows, "crisis_36", h003.FEATURES_CORE)
    assert fit["countries"] == 30 and fit["events"] > 50
    out = h003.loco(rows, "crisis_36", ("credit_gap",), ("credit_gap", *h003.FEATURES_CORE))
    assert out["auc_model"] > out["auc_base"] + 0.1
    assert out["precision10_model"] > out["base_rate"]
    first = h003.episodes(rows)[0]
    assert set(first) >= {"country", "period", "impulse", "crisis_36"}


def test_one_sided_gap_and_the_frozen_claims():
    trend = [100.0 + i for i in range(20)]
    gaps = h003.one_sided_gap([None, *trend[:-1], trend[-1] + 30])
    assert gaps[0] is None and gaps[h003.GAP_MIN_YEARS - 1] is None
    assert abs(gaps[h003.GAP_MIN_YEARS]) < 1e-6  # a straight line has no gap
    assert gaps[-1] > 15  # a jump shows, judged on the data up to it

    rows = synthetic_rows()
    for r in rows:
        r.outcomes["crisis_12"] = r.outcomes["crisis_36"]
        r.outcomes["drawdown_36"] = None  # not covered
    for r in rows[:1800]:
        r.panel = "jst"
    for r in rows[1800::2]:
        r.credit_gap = 12.0  # booms unrelated to crises here
    out = {(c["claim"], c["panel"], c["outcome"]): c for c in h003.claims(rows)}
    a = out[("a", "jst", "crisis_12")]
    assert a["ratio"] > 2 and a["verdict"] == "supported"
    assert out[("b", "bis", "drawdown_36")]["verdict"] == "no events"
    assert out[("b", "bis", "crisis_36")]["verdict"] in ("rejected", "not decided")
