"""Rule backtests on the point-in-time time series (timeseries.py).

A rule is one or more comparisons between series specs or numbers, joined by `and` / `or`
(`and` binds tighter):

    px:SPY > px:SPY|sma:200
    breadth:pct_above_200d > 50 and fred:BAMLH0A0HYM2|z:252 < 1
    cot:copper:managed_money:index < 90 or px:CPER|rsi:14 < 30

The rule is evaluated at each close with data known by then (series are causal and
point-in-time), and the position it implies is held over the next day: a signal on day t
earns the asset's return from t to t+1. Missing inputs mean no position. Switching costs
`cost_bps` of the traded notional each way (1.0 for a full long/flat switch, 2.0 for a
long/short flip).
"""

import math
import re
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from fin_intel import timeseries

_COMPARISON = re.compile(r"\s*(>=|<=|>|<)\s*")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")
TRADING_DAYS = 252


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class Clause:
    left: str
    op: str
    right: str  # a spec or a number


def parse_rule(rule: str) -> list[list[Clause]]:
    """Disjunction of conjunctions: [[a, b], [c]] means (a and b) or c."""
    groups = []
    for alternative in re.split(r"\s+or\s+", rule.strip()):
        clauses = []
        for text in re.split(r"\s+and\s+", alternative):
            parts = _COMPARISON.split(text.strip())
            if len(parts) != 3 or not parts[0] or not parts[2]:
                raise RuleError(f"{text!r}: expected '<series> <op> <series or number>'")
            clauses.append(Clause(parts[0], parts[1], parts[2]))
        groups.append(clauses)
    return groups


def _specs(groups: list[list[Clause]]) -> list[str]:
    specs = []
    for clauses in groups:
        for c in clauses:
            for side in (c.left, c.right):
                if not _NUMBER.match(side) and side not in specs:
                    specs.append(side)
    return specs


def _compare(a: float, op: str, b: float) -> bool:
    return {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[op]


def signals(groups: list[list[Clause]], series: dict[str, list], n: int) -> list[bool | None]:
    """The rule on each day: True, False, or None when an input is missing."""

    def value(side: str, i: int) -> float | None:
        return float(side) if _NUMBER.match(side) else series[side][i]

    out: list[bool | None] = []
    for i in range(n):
        any_true, any_unknown = False, False
        for clauses in groups:
            results = []
            for c in clauses:
                a, b = value(c.left, i), value(c.right, i)
                results.append(None if a is None or b is None else _compare(a, c.op, b))
            if all(r is True for r in results):
                any_true = True
            elif not any(r is False for r in results):
                any_unknown = True  # undecided: some inputs missing, none false
        out.append(True if any_true else None if any_unknown else False)
    return out


@dataclass(frozen=True)
class Stats:
    total_return: float
    cagr: float
    volatility: float
    sharpe: float | None
    max_drawdown: float


@dataclass(frozen=True)
class Trade:
    entry: date
    exit: date | None  # None while still open
    days: int
    ret: float


@dataclass
class Result:
    asset: str
    rule: str
    start: date
    end: date
    years: float
    strategy: Stats
    benchmark: Stats  # buy and hold over the same days
    exposure: float  # share of days with a position
    trades: int
    win_rate: float | None
    avg_trade: float | None
    by_year: list[dict] = field(default_factory=list)
    dates: list[date] = field(default_factory=list, repr=False)
    equity: list[float] = field(default_factory=list, repr=False)
    benchmark_equity: list[float] = field(default_factory=list, repr=False)
    trade_list: list[Trade] = field(default_factory=list, repr=False)


def _stats(returns: list[float], years: float) -> Stats:
    equity, peak, worst = 1.0, 1.0, 0.0
    for r in returns:
        equity *= 1 + r
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1)
    n = len(returns)
    mean = sum(returns) / n if n else 0.0
    var = sum((r - mean) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
    vol = math.sqrt(var * TRADING_DAYS)
    return Stats(
        total_return=equity - 1,
        cagr=equity ** (1 / years) - 1 if years > 0 and equity > 0 else -1.0,
        volatility=vol,
        sharpe=mean * TRADING_DAYS / vol if vol else None,
        max_drawdown=worst,
    )


def run(
    session: Session,
    asset: str,
    rule: str,
    start: date | None = None,
    end: date | None = None,
    cost_bps: float = 5.0,
    short: bool = False,
    pit: bool = True,
) -> Result:
    groups = parse_rule(rule)
    price_spec = f"px:{asset.upper()}"
    try:
        days, series = timeseries.build(
            session, [price_spec, *_specs(groups)], start=None, end=end, pit=pit
        )
    except timeseries.SpecError as exc:
        raise RuleError(str(exc)) from None
    prices = series[price_spec]
    signal = signals(groups, series, len(days))

    # Start once every input has data (on or after `start`): before that, a rule like
    # "a and b" with b missing would just sit flat and dilute the statistics.
    first = next(
        (
            i
            for i in range(len(days))
            if (start is None or days[i] >= start)
            and signal[i] is not None
            and all(v[i] is not None for v in series.values())
        ),
        None,
    )
    if first is None or first >= len(days) - 1:
        raise RuleError("no overlap between the asset's prices and the rule's inputs")

    cost = cost_bps / 10_000
    strat, bench = [], []
    positions: list[float] = []
    position, last_price = 0.0, prices[first]
    trades: list[Trade] = []
    entry_index, trade_equity = None, 1.0
    for i in range(first + 1, len(days)):
        # Decided at the previous close, from what was known then.
        s = signal[i - 1]
        target = 1.0 if s else -1.0 if short and s is False else 0.0
        price = prices[i]
        r = price / last_price - 1 if price is not None and last_price else 0.0
        if price is not None:
            last_price = price
        traded = abs(target - position)
        day = target * r - traded * cost
        if target != 0 and position == 0:
            entry_index, trade_equity = i - 1, 1.0
        if target != position and position != 0 and entry_index is not None:
            trades.append(
                Trade(days[entry_index], days[i - 1], i - 1 - entry_index, trade_equity - 1)
            )
            entry_index, trade_equity = (i - 1, 1.0) if target != 0 else (None, 1.0)
        trade_equity *= 1 + day
        position = target
        positions.append(position)
        strat.append(day)
        bench.append(r)
    if entry_index is not None:
        trades.append(Trade(days[entry_index], None, len(days) - 1 - entry_index, trade_equity - 1))

    period = days[first + 1 :]
    years = (days[-1] - days[first]).days / 365.25
    equity, bench_equity, e, b = [], [], 1.0, 1.0
    for rs, rb in zip(strat, bench, strict=True):
        e, b = e * (1 + rs), b * (1 + rb)
        equity.append(e)
        bench_equity.append(b)
    by_year: dict[int, list[tuple[float, float]]] = {}
    for d, rs, rb in zip(period, strat, bench, strict=True):
        by_year.setdefault(d.year, []).append((rs, rb))
    wins = [t for t in trades if t.ret > 0]
    return Result(
        asset=asset.upper(),
        rule=rule,
        start=days[first],
        end=days[-1],
        years=years,
        strategy=_stats(strat, years),
        benchmark=_stats(bench, years),
        exposure=sum(1 for p in positions if p != 0) / len(positions),
        trades=len(trades),
        win_rate=len(wins) / len(trades) if trades else None,
        avg_trade=sum(t.ret for t in trades) / len(trades) if trades else None,
        by_year=[
            {
                "year": year,
                "strategy": math.prod(1 + rs for rs, _ in rows) - 1,
                "benchmark": math.prod(1 + rb for _, rb in rows) - 1,
            }
            for year, rows in sorted(by_year.items())
        ],
        dates=period,
        equity=equity,
        benchmark_equity=bench_equity,
        trade_list=trades,
    )
