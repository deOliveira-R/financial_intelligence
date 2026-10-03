"""Market breadth: daily internals computed from our market-wide bars.

Universe "us_common": common stocks listed on NYSE, Nasdaq and NYSE American (OTC, ADRs,
ETFs and preferreds excluded). Closes are split-adjusted, so a 2-for-1 split isn't a 50%
decline. Each day counts every stock with a bar that day, delisted ones included once
their history is imported, so the measures aren't limited to today's survivors.

Fields available to timeseries specs as `breadth:<field>`: the stored counts plus
pct_above_50d, pct_above_200d, net_advances, ad_ratio, up_volume_ratio, net_new_highs.
"""

from collections import defaultdict, deque
from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import CorporateAction, DailyBar, MarketBreadth, Security

UNIVERSES = {
    "us_common": (Security.security_type == "CS") & Security.mic.in_(("XNYS", "XNAS", "XASE")),
}
SOURCE = "massive"  # the market-wide feed


def compute(session: Session, universe: str = "us_common") -> int:
    """Recompute the universe's breadth history from scratch; returns days written."""
    members = select(Security.id).where(UNIVERSES[universe])
    splits: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for security_id, ex_date, ratio in session.execute(
        select(CorporateAction.security_id, CorporateAction.ex_date, CorporateAction.value)
        .where(
            CorporateAction.action == "split",
            CorporateAction.source == SOURCE,
            CorporateAction.security_id.in_(members),
        )
        .distinct()
    ):
        splits[security_id].append((ex_date, ratio))

    days: dict[date, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    rows = session.execute(
        select(DailyBar.security_id, DailyBar.date, DailyBar.close, DailyBar.volume)
        .where(DailyBar.source == SOURCE, DailyBar.security_id.in_(members))
        .order_by(DailyBar.security_id, DailyBar.date)
    )
    current, bars = None, []
    for row in rows:
        if row.security_id != current:
            if bars:
                _accumulate(days, bars, splits.get(current, []))
            current, bars = row.security_id, []
        if row.close:
            bars.append((row.date, row.close, row.volume or 0))
    if bars:
        _accumulate(days, bars, splits.get(current, []))

    records, ad_line = [], 0
    for day in sorted(days):
        d = days[day]
        ad_line += int(d["advancers"] - d["decliners"])
        records.append(
            {
                "universe": universe,
                "date": day,
                **{k: int(d[k]) for k in INT_FIELDS},
                "ad_line": ad_line,
                "up_volume": d["up_volume"],
                "down_volume": d["down_volume"],
            }
        )
    session.execute(delete(MarketBreadth).where(MarketBreadth.universe == universe))
    upsert(session, MarketBreadth, records, key=["universe", "date"])
    session.commit()
    return len(records)


INT_FIELDS = (
    "count",
    "advancers",
    "decliners",
    "unchanged",
    "new_highs",
    "new_lows",
    "eligible_252",
    "above_50d",
    "eligible_50d",
    "above_200d",
    "eligible_200d",
)


def _accumulate(
    days: dict[date, dict[str, float]],
    bars: list[tuple[date, float, float]],
    splits: list[tuple[date, float]],
) -> None:
    """Add one stock's contribution to each day's counters."""
    # Split-adjust closes backwards so the series is continuous through splits. Bars before
    # an ex-date get its factor, whether or not the stock traded on the ex-date itself;
    # announced splits after the last bar haven't happened yet and are ignored.
    last_day = bars[-1][0]
    pending = sorted((s for s in splits if s[0] <= last_day), reverse=True)
    factor, adjusted, i = 1.0, [], 0
    for day, close, volume in reversed(bars):
        while i < len(pending) and pending[i][0] > day:
            factor /= pending[i][1]
            i += 1
        adjusted.append((day, close * factor, volume))
    adjusted.reverse()

    w50: deque[float] = deque(maxlen=50)
    w200: deque[float] = deque(maxlen=200)
    w252: deque[float] = deque(maxlen=252)
    sum50 = sum200 = 0.0
    prev = None
    for day, close, volume in adjusted:
        d = days[day]
        if prev is not None:
            d["count"] += 1
            if close > prev:
                d["advancers"] += 1
                d["up_volume"] += volume
            elif close < prev:
                d["decliners"] += 1
                d["down_volume"] += volume
            else:
                d["unchanged"] += 1
        if len(w50) == 50:
            sum50 -= w50[0]
        if len(w200) == 200:
            sum200 -= w200[0]
        w50.append(close)
        w200.append(close)
        w252.append(close)
        sum50 += close
        sum200 += close
        if len(w50) == 50:
            d["eligible_50d"] += 1
            d["above_50d"] += close > sum50 / 50
        if len(w200) == 200:
            d["eligible_200d"] += 1
            d["above_200d"] += close > sum200 / 200
        if len(w252) == 252:
            d["eligible_252"] += 1
            d["new_highs"] += close >= max(w252)
            d["new_lows"] += close <= min(w252)
        prev = close


def field_values(row: MarketBreadth, field: str) -> float | None:
    """A stored count or a derived ratio, by name."""
    derived = {
        "pct_above_50d": (row.above_50d, row.eligible_50d),
        "pct_above_200d": (row.above_200d, row.eligible_200d),
        "ad_ratio": (row.advancers, row.decliners),
        "up_volume_ratio": (row.up_volume, row.up_volume + row.down_volume),
    }
    if field in derived:
        num, den = derived[field]
        return num / den if den else None
    if field == "net_advances":
        return row.advancers - row.decliners
    if field == "net_new_highs":
        return row.new_highs - row.new_lows if row.eligible_252 else None
    if field in ("ad_line", "up_volume", "down_volume") or field in INT_FIELDS:
        return getattr(row, field)
    raise KeyError(field)


FIELDS = (
    *INT_FIELDS,
    "ad_line",
    "up_volume",
    "down_volume",
    "pct_above_50d",
    "pct_above_200d",
    "ad_ratio",
    "up_volume_ratio",
    "net_advances",
    "net_new_highs",
)
