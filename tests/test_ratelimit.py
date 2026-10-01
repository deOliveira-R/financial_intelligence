import pytest

from fin_intel.providers.ratelimit import Limit, QuotaExceededError, RateLimiter


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def test_sleeps_when_window_full():
    clock = FakeClock()
    limiter = RateLimiter([Limit(2, 1.0)], clock=clock, sleep=clock.sleep)
    for _ in range(3):
        limiter.acquire()
    assert clock.slept == [1.0]


def test_enforces_every_window():
    clock = FakeClock()
    limiter = RateLimiter([Limit(5, 1.0), Limit(6, 10.0)], clock=clock, sleep=clock.sleep)
    for _ in range(7):
        limiter.acquire()
    # 6th call waits for the 1s window, 7th for the 10s window.
    assert clock.slept == [1.0, 9.0]


def test_raises_instead_of_long_wait():
    clock = FakeClock()
    limiter = RateLimiter([Limit(1, 3600.0)], max_wait=60, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    with pytest.raises(QuotaExceededError):
        limiter.acquire()
