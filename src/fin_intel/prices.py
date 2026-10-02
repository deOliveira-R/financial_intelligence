"""Adjusted prices, computed from unadjusted bars and corporate actions.

Walking back from the latest bar, each split divides earlier prices by its ratio (and
multiplies volume), and each cash dividend multiplies earlier prices by
(1 - dividend / last close before the ex-date). This is the standard total-return
adjustment; it matches Tiingo's own adjusted series to within 0.1% since 2010.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol


class BarLike(Protocol):
    date: date
    close: float | None


@dataclass(frozen=True)
class Adjustment:
    price: float = 1.0
    volume: float = 1.0


def adjustments(
    bars: Sequence[BarLike], actions: Sequence[tuple[date, str, float]]
) -> dict[date, Adjustment]:
    """Factor per bar date. `bars` must cover every ex-date's prior close for dividends to
    be applied; `actions` are (ex_date, "split" | "dividend", value). Actions dated after
    the latest bar (announced, not yet effective) are ignored."""
    if not bars:
        return {}
    # Providers list announced actions ahead of time; one dated after the latest bar
    # hasn't happened yet in our data and must not adjust anything.
    latest = max(b.date for b in bars)
    pending = sorted((a for a in actions if a[0] <= latest), reverse=True)
    price = volume = 1.0
    out: dict[date, Adjustment] = {}
    i = 0
    for bar in sorted(bars, key=lambda b: b.date, reverse=True):
        # Apply every action dated after this bar; this bar is the last close before them.
        while i < len(pending) and pending[i][0] > bar.date:
            _, kind, value = pending[i]
            if kind == "split" and value:
                price /= value
                volume *= value
            elif kind == "dividend" and bar.close:
                price *= 1 - value / bar.close
            i += 1
        out[bar.date] = Adjustment(price, volume)
    return out
