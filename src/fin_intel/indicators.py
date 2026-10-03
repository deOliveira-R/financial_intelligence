"""Technical indicators on aligned series. Pure and causal: each value uses only the
current and earlier observations, so nothing leaks from the future into a backtest.

Series are lists of floats or None (missing). A window containing a missing value
yields None rather than silently shrinking.
"""

import math
from collections.abc import Callable
from datetime import date, timedelta

Series = list[float | None]


def _window(xs: Series, i: int, n: int) -> list[float] | None:
    """The n observations ending at i, or None if fewer exist or any is missing."""
    window = xs[max(0, i - n + 1) : i + 1]
    if len(window) < n or any(x is None for x in window):
        return None
    return [x for x in window if x is not None]


def sma(xs: Series, n: int) -> Series:
    return [None if (w := _window(xs, i, n)) is None else sum(w) / n for i in range(len(xs))]


def ema(xs: Series, n: int) -> Series:
    """Exponential moving average, seeded with the first full-window simple average."""
    alpha, out, prev = 2 / (n + 1), [], None
    seed = sma(xs, n)
    for x, s in zip(xs, seed, strict=True):
        if prev is None:
            prev = s
        elif x is not None:
            prev = alpha * x + (1 - alpha) * prev
        out.append(prev if x is not None else None)
    return out


def rsi(xs: Series, n: int = 14) -> Series:
    """Wilder's relative strength index (0-100)."""
    out: Series = [None] * len(xs)
    gains: list[float] = []
    losses: list[float] = []
    averages: tuple[float, float] | None = None  # (gain, loss), once n changes are seen
    for i in range(1, len(xs)):
        a, b = xs[i - 1], xs[i]
        if a is None or b is None:
            gains, losses, averages = [], [], None
            continue
        up, down = max(b - a, 0.0), max(a - b, 0.0)
        if averages is None:
            gains.append(up)
            losses.append(down)
            if len(gains) == n:
                averages = (sum(gains) / n, sum(losses) / n)
        else:
            averages = ((averages[0] * (n - 1) + up) / n, (averages[1] * (n - 1) + down) / n)
        if averages is not None:
            gain, loss = averages
            out[i] = 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)
    return out


def macd(xs: Series, fast: int = 12, slow: int = 26) -> Series:
    """MACD line: fast EMA minus slow EMA (signal line: `|macd|ema:9`)."""
    return [
        None if f is None or s is None else f - s
        for f, s in zip(ema(xs, fast), ema(xs, slow), strict=True)
    ]


def _pairs(xs: Series, n: int):
    """(index, current, n observations earlier), both present."""
    for i in range(n, len(xs)):
        now, then = xs[i], xs[i - n]
        if now is not None and then is not None:
            yield i, now, then


def ret(xs: Series, n: int = 1) -> Series:
    """Percentage change over n observations."""
    out: Series = [None] * len(xs)
    for i, now, then in _pairs(xs, n):
        out[i] = now / then - 1 if then else None
    return out


def diff(xs: Series, n: int = 1) -> Series:
    out: Series = [None] * len(xs)
    for i, now, then in _pairs(xs, n):
        out[i] = now - then
    return out


def _stdev(w: list[float]) -> float:
    mean = sum(w) / len(w)
    return math.sqrt(sum((x - mean) ** 2 for x in w) / (len(w) - 1))


def stdev(xs: Series, n: int) -> Series:
    return [None if (w := _window(xs, i, n)) is None else _stdev(w) for i in range(len(xs))]


def volatility(xs: Series, n: int = 21) -> Series:
    """Annualized volatility of daily returns over n observations."""
    return [None if s is None else s * math.sqrt(252) for s in stdev(ret(xs), n)]


def zscore(xs: Series, n: int = 252) -> Series:
    """How many standard deviations the value sits from its rolling mean."""
    means, devs = sma(xs, n), stdev(xs, n)
    return [
        None if x is None or m is None or not s else (x - m) / s
        for x, m, s in zip(xs, means, devs, strict=True)
    ]


def rolling_max(xs: Series, n: int) -> Series:
    return [None if (w := _window(xs, i, n)) is None else max(w) for i in range(len(xs))]


def rolling_min(xs: Series, n: int) -> Series:
    return [None if (w := _window(xs, i, n)) is None else min(w) for i in range(len(xs))]


def drawdown(xs: Series, n: int = 252) -> Series:
    """Distance below the rolling n-observation high (e.g. -0.2 = 20% below)."""
    return [
        None if x is None or h is None else x / h - 1
        for x, h in zip(xs, rolling_max(xs, n), strict=True)
    ]


def yoy(xs: Series, dates: list[date]) -> Series:
    """Change versus the value one year earlier (the latest date on or before date - 365d).
    Calendar-based, so it works the same for daily prices and forward-filled monthly data."""
    out: Series = []
    j = 0
    for i, d in enumerate(dates):
        target = d - timedelta(days=365)
        while j + 1 <= i and dates[j + 1] <= target:
            j += 1
        now, past = xs[i], (xs[j] if dates[j] <= target else None)
        out.append(None if now is None or not past else now / past - 1)
    return out


# name -> (function, takes a window argument, default window)
TRANSFORMS: dict[str, tuple[Callable, bool, int | None]] = {
    "sma": (sma, True, None),
    "ema": (ema, True, None),
    "rsi": (rsi, True, 14),
    "macd": (macd, False, None),
    "ret": (ret, True, 1),
    "diff": (diff, True, 1),
    "vol": (volatility, True, 21),
    "z": (zscore, True, 252),
    "high": (rolling_max, True, None),
    "low": (rolling_min, True, None),
    "dd": (drawdown, True, 252),
    "yoy": (yoy, False, None),
}
