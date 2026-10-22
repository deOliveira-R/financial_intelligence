from datetime import date

import numpy as np
import pytest

from fin_intel.studies import h008


def buy(cik, insider, day, filed=None):
    return h008.Purchase(cik, insider, day, filed or day)


def test_quarter_end_and_classification():
    assert h008.quarter_end(date(2023, 1, 15)) == date(2023, 3, 31)
    assert h008.quarter_end(date(2023, 12, 31)) == date(2023, 12, 31)
    assert h008.quarter_end(date(2024, 11, 2)) == date(2024, 12, 31)
    routine = [buy(1, "a", date(y, 3, 10)) for y in (2015, 2016, 2017, 2018)]
    opportunistic = [buy(1, "b", date(2016, 6, 1)), buy(1, "b", date(2018, 9, 5))]
    newcomer = [buy(1, "c", date(2018, 9, 20), filed=date(2018, 10, 2))]  # filed next quarter
    flags = h008.classify(routine + opportunistic + newcomer)
    assert flags[1, date(2018, 3, 31)]["routine"] == {"a"}  # the fourth March in a row
    assert "routine" not in flags[1, date(2016, 3, 31)]  # only one earlier March then
    assert flags[1, date(2018, 9, 30)]["opportunistic"] == {"b"}  # bought before, other months
    assert flags[1, date(2016, 6, 30)]["new"] == {"b"}  # nothing in the three prior years
    assert flags[1, date(2018, 12, 31)]["new"] == {"c"} and flags[1, date(2018, 12, 31)]["all"] == {
        "c"
    }


def test_returns_stay_within_a_cusip_and_screen_impossible_moves():
    q, later = date(2020, 3, 31), date(2020, 6, 30)
    prices = {
        q: h008.Price(10.0, 100.0, 1e3, None, "OLD"),
        later: h008.Price(80.0, 12.0, 1e3, None, "NEW"),
    }
    assert h008.forward_return(prices, {}, q, later) is None  # a retired CUSIP: no return
    same = {
        q: h008.Price(10.0, 100.0, 1e3, None, "A"),
        later: h008.Price(12.0, 100.0, 1e3, None, "A"),
    }
    assert h008.forward_return(same, {}, q, later) == pytest.approx(0.2)
    jump = {
        q: h008.Price(1.0, 100.0, 1e3, None, "A"),
        later: h008.Price(9.0, 100.0, 1e3, None, "A"),
    }
    assert h008.forward_return(jump, {}, q, later) is None  # +800%: a proxy error


def test_split_factor_recorded_and_inferred():
    before, after = h008.Price(100.0, 1000.0, 1e5, 7), h008.Price(50.0, 2000.0, 1e5, 7)
    assert (
        h008.split_factor(
            [(date(2020, 2, 1), 2.0)], date(2020, 1, 1), date(2020, 3, 31), before, after
        )
        == 2.0
    )
    assert (
        h008.split_factor(
            [(date(2020, 5, 1), 2.0)], date(2020, 1, 1), date(2020, 3, 31), before, after
        )
        == 1.0
    )
    assert (
        h008.split_factor(None, date(2020, 1, 1), date(2020, 3, 31), before, after) == 2.0
    )  # read off holdings
    drift = h008.Price(90.0, 1100.0, 1e5, None)
    assert h008.split_factor(None, date(2020, 1, 1), date(2020, 3, 31), before, drift) == 1.0
    reverse = h008.Price(1000.0, 100.0, 1e5, None)
    assert h008.split_factor(None, date(2020, 1, 1), date(2020, 3, 31), before, reverse) == 0.1


def synthetic(edge: float, seed: int = 5):
    """Forty quarters, 200 companies; opportunistic buyers appear in companies whose next
    quarter earns `edge` more than peers; routine buyers at random."""
    rng = np.random.default_rng(seed)
    ends = [h008.quarter_end(date(2013 + i // 4, 3 * (i % 4) + 1, 1)) for i in range(42)]
    prices, purchases = {}, []
    for cik in range(1, 201):
        level, by_q = 50.0 + cik % 7, {}
        if cik % 13 == 0:  # a routine buyer: every March since 2010, so routine from 2013
            purchases += [buy(cik, f"r{cik}", date(y, 3, 10)) for y in range(2010, 2024)]
        for i, q in enumerate(ends):
            by_q[q] = h008.Price(level, 1000.0, level * 1000.0 * (1 + cik % 10), None, str(cik))
            boost = 0.0
            if i >= 1 and cik % 13 and rng.random() < 0.06:
                purchases.append(buy(cik, f"o{cik}", date(q.year, q.month, 15)))
                boost = edge
            level *= 1 + boost + rng.normal(0.01, 0.08)
        prices[cik] = by_q
    sic = {cik: (2834 if cik % 2 else 7372) for cik in prices}
    return purchases, prices, sic, set(prices)


def test_signal_found_when_present_and_not_when_absent():
    window = (date(2014, 3, 31), date(2022, 12, 31))
    purchases, prices, sic, members = synthetic(edge=0.06)
    real = h008.analyze(purchases, prices, {}, sic, members, {}, window, reps=100)
    opp = real["h1"]["opportunistic"]
    assert opp["quarters"] >= 30 and opp["mean_excess"] > 0.03 and opp["t"] > 2
    assert real["h1"]["routine"]["mean_excess"] < opp["mean_excess"]
    assert real["h1"]["after_controls"]["adjusted_mean"] > 0
    assert real["verdict"] == "support"
    assert real["coverage"] == 1.0
    purchases, prices, sic, members = synthetic(edge=0.0, seed=9)
    none = h008.analyze(purchases, prices, {}, sic, members, {}, window, reps=100)
    assert none["verdict"] in ("rejection", "weak")
    assert "h4" in none and none["h1"]["exits"]["without_price_at_horizon"] == 0
    assert none["h1"]["exits"]["screened_as_proxy_error"] == 0
