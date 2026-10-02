from dataclasses import dataclass
from datetime import date

import pytest

from fin_intel.prices import adjustments


@dataclass
class Bar:
    date: date
    close: float


def test_split_adjusts_earlier_prices_and_volumes():
    bars = [Bar(date(2026, 1, 1), 100), Bar(date(2026, 1, 2), 50)]
    f = adjustments(bars, [(date(2026, 1, 2), "split", 2.0)])
    assert (f[date(2026, 1, 1)].price, f[date(2026, 1, 1)].volume) == (0.5, 2.0)
    assert (f[date(2026, 1, 2)].price, f[date(2026, 1, 2)].volume) == (1.0, 1.0)


def test_dividend_uses_last_close_before_ex_date():
    bars = [Bar(date(2026, 1, 1), 100), Bar(date(2026, 1, 2), 99), Bar(date(2026, 1, 5), 98)]
    f = adjustments(bars, [(date(2026, 1, 5), "dividend", 1.0)])
    assert f[date(2026, 1, 2)].price == pytest.approx(1 - 1 / 99)
    assert f[date(2026, 1, 1)].price == pytest.approx(1 - 1 / 99)
    assert f[date(2026, 1, 5)].price == 1.0


def test_actions_compound():
    bars = [Bar(date(2026, 1, 1), 200), Bar(date(2026, 1, 2), 100), Bar(date(2026, 1, 3), 100)]
    actions = [(date(2026, 1, 2), "split", 2.0), (date(2026, 1, 3), "dividend", 1.0)]
    f = adjustments(bars, actions)
    assert f[date(2026, 1, 1)].price == pytest.approx(0.5 * (1 - 1 / 100))


def test_announced_actions_after_latest_bar_are_ignored():
    bars = [Bar(date(2026, 10, 1), 10.0)]
    actions = [(date(2026, 10, 2), "split", 0.0002), (date(2026, 10, 5), "dividend", 1.0)]
    assert adjustments(bars, actions)[date(2026, 10, 1)].price == 1.0
