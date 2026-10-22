from datetime import date

import numpy as np

from fin_intel.studies import h008, h010

Q = [h008.quarter_end(date(2013 + i // 4, 3 * (i % 4) + 1, 1)) for i in range(40)]


def test_next_quarter_end_and_eligibility():
    assert h010.next_quarter_end(date(2016, 12, 31)) == date(2017, 3, 31)
    assert h010.next_quarter_end(date(2017, 3, 31)) == date(2017, 6, 30)
    books = {
        1: {q: (20, 2e9, 0.1) for q in Q[:6]},  # concentrated, big, patient, with history
        2: {q: (80, 5e9, 0.1) for q in Q[:6]},  # too many names
        3: {q: (20, 2e9, 0.4) for q in Q[:6]},  # churns
        4: {Q[5]: (20, 2e9, 0.1)},  # no history
        5: {q: (20, 5e8, 0.1) for q in Q[:6]},  # too small
    }
    assert h010.eligible(books, 1, Q[5]) and not h010.eligible(books, 1, Q[2])  # needs 4 before
    assert not any(h010.eligible(books, f, Q[5]) for f in (2, 3, 4, 5))


def test_flags_distinguish_screened_managers_from_everyone():
    books = {
        1: {q: (20, 2e9, 0.1) for q in Q[:8]},
        2: {q: (20, 2e9, 0.1) for q in Q[:8]},
        9: {q: (300, 9e9, 0.3) for q in Q[:8]},  # an index-like filer: not eligible
    }
    ideas = [
        (1, Q[5], "AAA"),
        (2, Q[5], "AAA"),
        (9, Q[5], "AAA"),
        (9, Q[5], "BBB"),
        (1, Q[5], "ZZZ"),
    ]
    cusip_cik = {("AAA", Q[5]): 100, ("BBB", Q[5]): 200}  # ZZZ has no issuer
    flags = h010.build_flags(books, ideas, cusip_cik)
    entry = h010.next_quarter_end(Q[5])
    assert flags[100, entry]["best_idea"] == {"1", "2"} and flags[100, entry]["consensus"] == {
        "1",
        "2",
    }
    assert flags[100, entry]["any_manager"] == {"1", "2", "9"}
    assert flags[200, entry]["any_manager"] == {"9"} and "best_idea" not in flags[200, entry]
    assert all(cik != 0 for cik, _ in flags)


def synthetic(edge: float, seed: int = 11):
    """Two hundred companies over Q; screened managers' best ideas land on companies that
    earn `edge` more over the following four quarters; an unscreened filer's picks don't."""
    rng = np.random.default_rng(seed)
    prices, ideas, cusip_cik = {}, [], {}
    books = {f: {q: (20, 2e9, 0.1) for q in Q} for f in (1, 2, 3)}
    books[9] = {q: (400, 9e9, 0.2) for q in Q}
    for cik in range(1, 201):
        level, by_q, boost_until = 50.0 + cik % 7, {}, -1
        for i, q in enumerate(Q):
            by_q[q] = h008.Price(level, 1000.0, level * 1000.0 * (1 + cik % 10), None, str(cik))
            cusip_cik[str(cik), q] = cik
            if 4 <= i < len(Q) - 6 and rng.random() < 0.05:
                ideas.append((int(rng.integers(1, 4)), q, str(cik)))
                boost_until = i + 5  # the edge accrues over the four quarters after entry
            if rng.random() < 0.05:
                ideas.append((9, q, str(cik)))
            boost = edge / 4 if i + 1 <= boost_until and i + 1 > boost_until - 4 else 0.0
            level *= 1 + boost + rng.normal(0.01, 0.08)
        prices[cik] = by_q
    sic = {cik: (2834 if cik % 2 else 7372) for cik in prices}
    return books, ideas, cusip_cik, prices, sic, set(prices)


def test_signal_found_when_present_and_not_when_absent():
    window = (date(2015, 3, 31), date(2021, 12, 31))
    books, ideas, cusip_cik, prices, sic, members = synthetic(edge=0.20)
    flags = h010.build_flags(books, ideas, cusip_cik)
    real = h010.analyze(flags, prices, {}, sic, members, {}, window, reps=100)
    best, anyone = real["h4"]["best_idea"], real["h4"]["any_manager"]
    assert best["quarters"] >= 20 and best["mean_excess"] > 0.05 and best["t"] > 2
    assert best["mean_excess"] > anyone["mean_excess"]
    assert real["h4"]["after_controls"]["adjusted_mean"] > 0
    assert real["verdict"] == "support"
    books, ideas, cusip_cik, prices, sic, members = synthetic(edge=0.0, seed=12)
    none = h010.analyze(
        h010.build_flags(books, ideas, cusip_cik), prices, {}, sic, members, {}, window, reps=100
    )
    assert none["verdict"] in ("rejection", "weak")
