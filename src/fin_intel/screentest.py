"""Backtest stock screens on point-in-time metrics (metrics.py, `backfill-metrics`).

On each date with stored metrics, the screen picks its top names from what was known that
day; the picks are bought at the next close in equal weight and held for each horizon.
Their return is compared with SPY and with the screen's universe (every company with
metrics that day, equal weight): beating the universe is what shows the screen adds
something, since small and cheap stocks as a group can beat SPY on their own.

A pick delisted during the holding period keeps its last price (cash afterwards): a
bankruptcy shows as a loss and a takeover as a gain, with no survivorship bias.
"""

from dataclasses import dataclass, field
from datetime import date
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import screener, timeseries
from fin_intel.models import CompanyMetrics

HORIZONS = (63, 126, 252)  # trading days: about 3, 6 and 12 months


@dataclass
class Period:
    as_of: date
    picks: list[str]
    returns: dict[int, float | None]  # horizon -> equal-weight return of the picks
    universe: dict[int, float | None]
    spy: dict[int, float | None]


@dataclass(frozen=True)
class Summary:
    horizon: int
    periods: int
    mean_return: float | None
    vs_universe: float | None  # mean excess over the universe
    vs_spy: float | None
    beat_universe: float | None  # share of periods beating it


@dataclass
class Result:
    screen: str
    top: int
    periods: list[Period] = field(default_factory=list)
    summary: list[Summary] = field(default_factory=list)


class _Prices:
    """Adjusted closes per security on the trading calendar, loaded once each."""

    def __init__(self, session: Session):
        self.session = session
        self.days = timeseries.calendar(session, None, None)
        self.index = {d: i for i, d in enumerate(self.days)}
        self.cache: dict[int, list[float | None] | None] = {}

    def series(self, security_id: int) -> list[float | None] | None:
        if security_id not in self.cache:
            try:
                self.cache[security_id] = timeseries.security_prices(
                    self.session, security_id, self.days
                )
            except timeseries.SpecError:
                self.cache[security_id] = None
        return self.cache[security_id]

    def forward(self, px: list[float | None], start: int, horizon: int) -> float | None:
        """Return from `start` over `horizon` days; a series that stops (delisting) keeps
        its last price. None if the horizon isn't over yet."""
        first = px[start]
        if first is None or start + horizon >= len(self.days):
            return None
        last = next(
            (v for i in range(start + horizon, start, -1) if (v := px[i]) is not None), first
        )
        return last / first - 1


def run(
    session: Session,
    preset: str | None = None,
    filters: list[str] | None = None,
    rank: str | None = None,
    sort: str | None = None,
    top: int = 20,
    horizons: tuple[int, ...] = HORIZONS,
    exclude_sectors: list[str] | None = None,
) -> Result:
    prices = _Prices(session)
    spy = timeseries._price(session, "SPY", prices.days, "px")
    dates = sorted(set(session.scalars(select(CompanyMetrics.as_of).distinct())))
    result = Result(screen=preset or " ".join(filters or []) or (rank or sort or ""), top=top)
    for as_of in dates:
        # Bought at the first close after the screen ran.
        start = next((prices.index[d] for d in prices.days if d > as_of), None)
        if start is None:
            continue
        _, picks = screener.screen(
            session,
            filters,
            sort,
            rank,
            preset,
            top,
            exclude_sectors=exclude_sectors,
            as_of=as_of,
        )
        if not picks:
            continue
        universe_ids = session.scalars(
            select(CompanyMetrics.security_id).where(CompanyMetrics.as_of == as_of)
        ).all()

        def basket(ids: list[int], h: int, start: int = start) -> float | None:
            rets = [
                r
                for sid in ids
                if (px := prices.series(sid)) is not None
                and (r := prices.forward(px, start, h)) is not None
            ]
            return mean(rets) if rets else None

        result.periods.append(
            Period(
                as_of=as_of,
                picks=[p["ticker"] or f"#{p['security_id']}" for p in picks],
                returns={h: basket([p["security_id"] for p in picks], h) for h in horizons},
                universe={h: basket(list(universe_ids), h) for h in horizons},
                spy={h: prices.forward(spy, start, h) for h in horizons},
            )
        )
    for h in horizons:
        rets, vs_u, vs_s = [], [], []
        for p in result.periods:
            r, u, b = p.returns[h], p.universe[h], p.spy[h]
            if r is None:
                continue
            rets.append(r)
            if u is not None:
                vs_u.append(r - u)
            if b is not None:
                vs_s.append(r - b)
        result.summary.append(
            Summary(
                horizon=h,
                periods=len(rets),
                mean_return=mean(rets) if rets else None,
                vs_universe=mean(vs_u) if vs_u else None,
                vs_spy=mean(vs_s) if vs_s else None,
                beat_universe=sum(1 for x in vs_u if x > 0) / len(vs_u) if vs_u else None,
            )
        )
    return result
