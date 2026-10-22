from datetime import date

import numpy as np

from fin_intel.studies import h007


def synthetic_months(n: int, beta: float, seed: int = 3) -> list[h007.Month]:
    """Months whose put premium rises with the VRP by `beta` per point around its mean of 4
    (0: no relation), so writing puts below the median loses money on average."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        vrp = float(rng.normal(4, 4))
        premium = beta * (vrp - 4) + float(rng.normal(0, 0.03))
        rf = 0.002
        out.append(
            h007.Month(
                end=date(1993 + i // 12, i % 12 + 1, 28),
                vrp=vrp,
                slope=None,
                put_ret=rf + premium,
                spy_ret=rf + float(rng.normal(0.005, 0.04)),
                rf=rf,
            )
        )
    return out


def test_signal_is_found_when_present_and_not_when_absent():
    window = (date(2000, 1, 1), date(2030, 1, 1))
    real = h007.analyze(synthetic_months(360, beta=0.004), window, reps=200)
    assert real["regression_vrp"]["slope"] > 0 and real["regression_vrp"]["p"] < 0.01
    assert real["sorts_vrp"]["top"]["mean"] > real["sorts_vrp"]["bottom"]["mean"]
    assert real["rule_vrp"]["rule"]["sharpe"] > real["rule_vrp"]["always_put"]["sharpe"]
    assert h007.verdict(real) in ("support", "partial")
    none = h007.analyze(synthetic_months(360, beta=0.0), window, reps=200)
    assert none["regression_vrp"]["verdict"] in ("no", "weak")
    assert h007.verdict(none) != "support"
    assert none["term_structure"]["rule"] == {"months": 0}  # no slope data


def test_realized_vol_and_expanding_rank():
    prices = [100 * (1.01 ** (i % 2)) for i in range(30)]  # alternating +-1%
    rv = h007.realized_vol(prices, 29)
    assert rv is not None and 15 < rv < 17  # about 1% a day, annualized
    assert h007.realized_vol(prices, 10) is None
    values = [float(i) for i in range(70)]
    rank, med = h007.expanding_rank(values, 69)
    assert rank == 1.0 and med == 34.0
    assert h007.expanding_rank(values, 10) == (None, None)
