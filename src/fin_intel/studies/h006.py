"""H006: a trend and credit regime filter cuts drawdowns at little cost in return
(docs/research/H006-regime-filter.md).

Three rules (trend: the asset above its 200-day average; credit: the Baa spread not above
its own 200-day average; both), decided at each month's last close and held for the
following month. Cash earns the 3-month T-bill; each position change costs 5 basis points.
Judged against buy and hold and against a constant mix at the rule's own average exposure,
on monthly return triples with a moving-block bootstrap. The whole span is simulated once
and each period reads its own slice, so a period starts with the position decided at the
month end before it, as a running strategy would.
"""

import math
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from fin_intel import indicators, research, timeseries
from fin_intel.studies import h002

HYPOTHESIS = "H006"
ASSETS = ("SPY", "RSP")
SPREAD = "fred:BAA10Y"
TBILL = "fred:DGS3MO"
RULES = ("trend", "credit", "both")
WINDOW = 200
ROBUSTNESS_WINDOWS = (150, 250)
COST_BPS = 5.0
BLOCK_MONTHS = 24
PERIODS = {
    "design": (date(1993, 12, 1), date(2015, 12, 31)),  # first full decision 1993-11-30
    "validation": (date(2016, 1, 1), date(2023, 6, 30)),
    "holdout": (research.HOLDOUT_START, date(2100, 1, 1)),
}
EXPANDING_FROM = 2005
# Set after the design run (the pre-registration's choice rule), before validation.
FROZEN: dict[str, Any] | None = None


@dataclass
class Simulation:
    rule: str
    window: int
    frequency: str
    days: list[date]
    strategy: list[float]  # daily returns, from days[1]
    hold: list[float]
    cash: list[float]
    position: list[float]  # position held over each return day
    signal: list[bool | None]  # the decision governing it (None: inputs missing)
    switches: list[bool]


def lag(series: indicators.Series) -> indicators.Series:
    """Yesterday's value: what FRED had posted by this morning for an unrevised daily series."""
    return [None, *series[:-1]]


def decide(
    rule: str, price: float | None, price_avg: float | None, spread: float | None,
    spread_avg: float | None,
) -> bool | None:  # fmt: skip
    trend = None if price is None or price_avg is None else price > price_avg
    credit = None if spread is None or spread_avg is None else spread <= spread_avg
    if rule == "trend":
        return trend
    if rule == "credit":
        return credit
    return None if trend is None or credit is None else trend and credit


def simulate(
    days: list[date],
    prices: indicators.Series,
    spread: indicators.Series,
    tbill: indicators.Series,
    rule: str,
    window: int = WINDOW,
    frequency: str = "monthly",
    cost_bps: float = COST_BPS,
) -> Simulation:
    """Run one rule over the whole span; `spread` and `tbill` are as known at each close."""
    price_avg = indicators.sma(prices, window)
    spread_avg = indicators.sma(spread, window)
    decision_days = set(h002.month_ends(days)) if frequency == "monthly" else set(range(len(days)))
    cost = cost_bps / 10_000
    strategy, hold, cash, position, signals, switches = [], [], [], [], [], []
    held, signal = 0.0, None
    for i in range(1, len(days)):
        p0, p1 = prices[i - 1], prices[i]
        r = p1 / p0 - 1 if p0 and p1 else 0.0
        yield_ = tbill[i - 1]
        c = yield_ / 100 / 252 if yield_ is not None else 0.0
        if i - 1 in decision_days:
            signal = decide(rule, prices[i - 1], price_avg[i - 1], spread[i - 1], spread_avg[i - 1])
            target = 1.0 if signal else 0.0  # missing inputs: no position
        else:
            target = held
        switched = target != held
        strategy.append(target * r + (1 - target) * c - (cost if switched else 0.0))
        hold.append(r)
        cash.append(c)
        position.append(target)
        signals.append(signal)
        switches.append(switched)
        held = target
    return Simulation(
        rule, window, frequency, days, strategy, hold, cash, position, signals, switches
    )


def constant_mix(
    days: list[date],
    hold: list[float],
    cash: list[float],
    weight: float,
    cost_bps: float = COST_BPS,
) -> list[float]:
    """A fixed asset weight rebalanced at month ends (the weight drifts within the month)."""
    ends = set(h002.month_ends(days))
    cost = cost_bps / 10_000
    out, w = [], weight
    for k, (r, c) in enumerate(zip(hold, cash, strict=True)):
        day = w * r + (1 - w) * c
        w = w * (1 + r) / (1 + day) if day > -1 else weight
        if k + 1 in ends:
            day -= abs(w - weight) * cost
            w = weight
        out.append(day)
    return out


# --- statistics ---


def equity(returns: list[float]) -> list[float]:
    out, e = [], 1.0
    for r in returns:
        e *= 1 + r
        out.append(e)
    return out


def max_drawdown(returns: list[float]) -> float:
    peak, worst = 1.0, 0.0
    for e in equity(returns):
        peak = max(peak, e)
        worst = min(worst, e / peak - 1)
    return worst


def cagr(returns: list[float], years: float) -> float:
    total = math.prod(1 + r for r in returns)
    return total ** (1 / years) - 1 if years > 0 and total > 0 else math.nan


def worst_12m(returns: list[float], window: int = 252) -> float | None:
    if len(returns) < window:
        return None
    e = [1.0, *equity(returns)]
    return min(e[i + window] / e[i] - 1 for i in range(len(e) - window))


def monthly(days: list[date], returns: list[float]) -> list[float]:
    """Compound daily returns (from days[1]) within calendar months."""
    out, acc, current = [], 1.0, None
    for d, r in zip(days[1:], returns, strict=True):
        if current is not None and (d.year, d.month) != current:
            out.append(acc - 1)
            acc = 1.0
        acc *= 1 + r
        current = (d.year, d.month)
    if current is not None:
        out.append(acc - 1)
    return out


def describe(returns: list[float], cash: list[float], years: float) -> dict[str, Any]:
    excess = [r - c for r, c in zip(returns, cash, strict=True)]
    sd = float(np.std(returns, ddof=1)) if len(returns) > 2 else math.nan
    sd_excess = float(np.std(excess, ddof=1)) if len(excess) > 2 else math.nan
    return {
        "cagr": cagr(returns, years),
        "volatility": sd * math.sqrt(252),
        "sharpe": float(np.mean(excess)) / sd_excess * math.sqrt(252) if sd_excess else math.nan,
        "max_drawdown": max_drawdown(returns),
        "worst_12m": worst_12m(returns),
    }


def _slice(sim: Simulation, window: tuple[date, date]) -> list[int]:
    """Return days (indexes into the daily lists) in the period: from the month-end decision
    governing its first month (the last month end on or before its start, once the rule's
    inputs exist) through its end, as a running strategy would experience it."""
    ends = [i for i in h002.month_ends(sim.days) if i < len(sim.strategy)]
    before = [i for i in ends if sim.days[i] <= window[0]]
    first = before[-1] if before else (ends[0] if ends else None)
    if first is None:
        return []
    while first in ends and sim.signal[first] is None:
        first = ends[ends.index(first) + 1] if ends.index(first) + 1 < len(ends) else None
        if first is None:
            return []
    return [k for k in range(first, len(sim.strategy)) if sim.days[k + 1] <= window[1]]


def evaluate(sim: Simulation, window: tuple[date, date], reps: int = 2000) -> dict[str, Any] | None:
    """One rule on one asset over a period: strategy, buy and hold and the constant mix,
    with bootstrap intervals for the drawdown ratio and the return gap."""
    idx = _slice(sim, window)
    if len(idx) < 252:
        return None
    days = [sim.days[idx[0]], *(sim.days[k + 1] for k in idx)]
    strat = [sim.strategy[k] for k in idx]
    hold = [sim.hold[k] for k in idx]
    cash = [sim.cash[k] for k in idx]
    exposure = float(np.mean([sim.position[k] for k in idx]))
    mix = constant_mix(days, hold, cash, exposure)
    years = (days[-1] - days[0]).days / 365.25
    triples = list(zip(monthly(days, strat), monthly(days, hold), monthly(days, mix), strict=True))
    n_months = len(triples)

    def dd_ratio(rows: list[tuple[float, float, float]], j: int) -> float:
        a, b = max_drawdown([r[0] for r in rows]), max_drawdown([r[j] for r in rows])
        return a / b if b else math.nan

    def gap(rows: list[tuple[float, float, float]], j: int) -> float:
        yrs = len(rows) / 12
        return cagr([r[0] for r in rows], yrs) - cagr([r[j] for r in rows], yrs)

    def interval(stat) -> dict[str, float | None]:
        est = research.block_bootstrap(triples, stat, BLOCK_MONTHS, reps=reps)
        return {"value": est.value, "low": est.low, "high": est.high}

    diff_hold = research.newey_west_mean([a - b for a, b, _ in triples])
    diff_mix = research.newey_west_mean([a - c for a, _, c in triples])
    return {
        "start": days[0],
        "end": days[-1],
        "years": years,
        "months": n_months,
        "strategy": describe(strat, cash, years),
        "hold": describe(hold, cash, years),
        "constant_mix": describe(mix, cash, years),
        "exposure": exposure,
        "switches_per_year": sum(sim.switches[k] for k in idx) / years,
        "drawdown_ratio_vs_hold": interval(lambda rows: dd_ratio(rows, 1)),
        "cagr_gap_vs_hold": interval(lambda rows: gap(rows, 1)),
        "drawdown_ratio_vs_mix": interval(lambda rows: dd_ratio(rows, 2)),
        "cagr_gap_vs_mix": interval(lambda rows: gap(rows, 2)),
        "monthly_diff_vs_hold": {"mean": diff_hold.value, "t": diff_hold.t},
        "monthly_diff_vs_mix": {"mean": diff_mix.value, "t": diff_mix.t},
    }


def expanding_view(sim: Simulation, window: tuple[date, date]) -> list[dict[str, Any]]:
    """What the evidence to each year end said (drawdown ratio and return gap to date)
    and what the next year then did, strategy against buy and hold."""
    out = []
    for year in range(EXPANDING_FROM, window[1].year):
        to_date = evaluate(sim, (window[0], date(year, 12, 31)), reps=200)
        next_year = _slice(sim, (date(year + 1, 1, 1), date(year + 1, 12, 31)))
        if to_date is None or not next_year:
            continue
        out.append(
            {
                "through": year,
                "drawdown_ratio": to_date["drawdown_ratio_vs_hold"]["value"],
                "cagr_gap": to_date["cagr_gap_vs_hold"]["value"],
                "next_year_strategy": math.prod(1 + sim.strategy[k] for k in next_year) - 1,
                "next_year_hold": math.prod(1 + sim.hold[k] for k in next_year) - 1,
            }
        )
    return out


def verdict(result: dict[str, Any]) -> str:
    """The pre-registered reading of one asset's result."""
    dd = result["drawdown_ratio_vs_hold"]
    gap = result["cagr_gap_vs_hold"]
    better_than_mix = (
        result["strategy"]["max_drawdown"] > result["constant_mix"]["max_drawdown"]
        and result["strategy"]["cagr"] > result["constant_mix"]["cagr"]
    )
    if dd["value"] <= 2 / 3 and (dd["high"] or 1) < 1 and gap["value"] >= -0.01 and \
            (gap["high"] or 0) >= 0 and better_than_mix:  # fmt: skip
        return "support"
    if dd["value"] > 0.85 or gap["value"] < -0.02 or not better_than_mix:
        return "rejection"
    return "partial"


def choose(results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The design's choice rule: among the primary variants (200 days, monthly), the largest
    SPY drawdown reduction with a return gap within a point."""
    eligible = [
        r for r in results
        if r["window"] == WINDOW and r["frequency"] == "monthly"
        and r["assets"].get("SPY") and r["assets"]["SPY"]["cagr_gap_vs_hold"]["value"] >= -0.01
    ]  # fmt: skip
    if not eligible:
        return None
    best = min(eligible, key=lambda r: r["assets"]["SPY"]["drawdown_ratio_vs_hold"]["value"])
    return {"rule": best["rule"], "window": best["window"], "frequency": best["frequency"]}


def load(session: Session, end: date) -> tuple[list[date], dict[str, indicators.Series]]:
    specs = [*(f"px:{a}" for a in ASSETS), SPREAD, TBILL]
    days, series = timeseries.build(session, specs, None, end, pit=False)
    series[SPREAD] = lag(series[SPREAD])
    series[TBILL] = lag(series[TBILL])
    return days, series


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in PERIODS:
        raise ValueError(f"period is one of {tuple(PERIODS)}")
    frozen = FROZEN
    if period != "design":
        if frozen is None:
            raise research.HoldoutError("set FROZEN from the design's choice before validation")
        if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, frozen):
            raise research.HoldoutError("freeze the variant first (study H006 --freeze), once")
    window = PERIODS[period]
    end = research.HOLDOUT_START if period != "holdout" else date.today()
    days, series = load(session, end)
    if period != "holdout":
        research.check_period(period, days[-1])
    if period == "design":
        variants = [(rule, WINDOW, "monthly") for rule in RULES]
        variants += [(rule, w, "monthly") for w in ROBUSTNESS_WINDOWS for rule in RULES]
        variants += [(rule, WINDOW, "daily") for rule in RULES]
    else:
        assert frozen is not None
        variants = [(frozen["rule"], frozen["window"], frozen["frequency"])]
    results = []
    for rule, win, frequency in variants:
        variant = {"rule": rule, "window": win, "frequency": frequency}
        assets: dict[str, Any] = {}
        for asset in ASSETS:
            sim = simulate(days, series[f"px:{asset}"], series[SPREAD], series[TBILL], rule, win,
                           frequency)  # fmt: skip
            result = evaluate(sim, window)
            if result is not None:
                result["verdict"] = verdict(result)
                if period != "holdout":
                    result["expanding"] = expanding_view(sim, window)
            assets[asset] = result
        record = {"period": period, **variant, "assets": assets}
        results.append(record)
        if log:
            research.log(session, HYPOTHESIS, variant, period, {"assets": assets})
    if period == "design":
        results.append({"period": period, "suggested_frozen": choose(results)})
    return results
