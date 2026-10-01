import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from fin_intel.providers.errors import QuotaExceededError


@dataclass(frozen=True)
class Limit:
    calls: int
    period: float  # seconds


SECOND, MINUTE, HOUR, DAY = 1.0, 60.0, 3600.0, 86400.0


class RateLimiter:
    """Sliding-window limiter enforcing several windows at once (e.g. 50/hour and 1000/day).

    Short waits are slept through; a wait longer than `max_wait` raises QuotaExceededError
    so batch jobs fail fast instead of blocking for hours on a daily quota.
    Counters are in-process only.
    """

    def __init__(
        self,
        limits: Iterable[Limit],
        max_wait: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._windows = [(limit, deque[float]()) for limit in limits]
        self.max_wait = max_wait
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def _wait_time(self, now: float) -> float:
        wait = 0.0
        for limit, calls in self._windows:
            while calls and calls[0] <= now - limit.period:
                calls.popleft()
            if len(calls) >= limit.calls:
                wait = max(wait, calls[0] + limit.period - now)
        return wait

    @property
    def longest_period(self) -> float:
        return max((limit.period for limit, _ in self._windows), default=0.0)

    def record_past(self, seconds_ago: Iterable[float]) -> None:
        """Count calls made earlier (e.g. by previous processes) against the windows."""
        now = self._clock()
        with self._lock:
            for ago in sorted(seconds_ago, reverse=True):
                for _, calls in self._windows:
                    calls.append(now - ago)

    def acquire(self) -> None:
        with self._lock:
            while (wait := self._wait_time(self._clock())) > 0:
                if wait > self.max_wait:
                    raise QuotaExceededError(f"rate limit reached; next slot in {wait:.0f}s")
                self._sleep(wait)
            now = self._clock()
            for _, calls in self._windows:
                calls.append(now)
