"""H009: volatility-managed exposure earns more per unit of risk than a fixed allocation
(docs/research/H009-volatility-managed.md).

At each month's last close the equity weight for the next month is min(1, target /
realized volatility), realized volatility being the annualized standard deviation of the
last 21 daily log returns; cash earns the 3-month T-bill. The simulation, benchmarks
(buy and hold, the constant mix at the rule's average exposure) and statistics are
H006's; only the decision and the verdict differ.
"""

import math
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from fin_intel import indicators, research
from fin_intel.studies import h002, h006

HYPOTHESIS = "H009"
TARGET = 0.12
WINDOW = 21
ROBUSTNESS = ((63, TARGET), (WINDOW, 0.15))
PERIODS = h006.PERIODS
FROZEN = {"rule": "inverse volatility", "target": TARGET, "window": WINDOW, "cap": 1.0}


def realized_vol(prices: indicators.Series, i: int, window: int) -> float | None:
    chunk = prices[i - window : i + 1]
    if len(chunk) < window + 1 or any(p is None or p <= 0 for p in chunk):
        return None
    logs = np.diff(np.log(np.asarray(chunk, dtype=float)))
    return float(np.std(logs, ddof=1) * math.sqrt(252))


def weight(vol: float | None, target: float = TARGET, cap: float = 1.0) -> float | None:
    if vol is None:
        return None
    return min(cap, target / vol) if vol > 0 else cap


def simulate(
    days: list[date],
    prices: indicators.Series,
    tbill: indicators.Series,
    target: float = TARGET,
    window: int = WINDOW,
    cost_bps: float = h006.COST_BPS,
) -> h006.Simulation:
    """H006's simulation with a continuous weight decided at month ends: a missing
    volatility means no position, a change of weight costs on the fraction traded."""
    decision_days = set(h002.month_ends(days))
    cost = cost_bps / 10_000
    strategy, hold, cash, position, signals, switches = [], [], [], [], [], []
    held, signal = 0.0, None
    for i in range(1, len(days)):
        p0, p1 = prices[i - 1], prices[i]
        r = p1 / p0 - 1 if p0 and p1 else 0.0
        yield_ = tbill[i - 1]
        c = yield_ / 100 / 252 if yield_ is not None else 0.0
        if i - 1 in decision_days:
            w = weight(realized_vol(prices, i - 1, window), target)
            signal = None if w is None else w > 0
            held_target = w if w is not None else 0.0
        else:
            held_target = held
        traded = abs(held_target - held)
        strategy.append(held_target * r + (1 - held_target) * c - traded * cost)
        hold.append(r)
        cash.append(c)
        position.append(held_target)
        signals.append(signal)
        switches.append(traded > 0.01)  # a rebalance of more than a point
        held = held_target
    return h006.Simulation(
        "inverse volatility", window, "monthly", days, strategy, hold, cash, position, signals,
        switches,
    )  # fmt: skip


def verdict(result: dict[str, Any]) -> str:
    """The registered reading of one asset's result."""
    s, h, m = result["strategy"], result["hold"], result["constant_mix"]
    dd = result["drawdown_ratio_vs_hold"]
    sharpe_mix = result["sharpe_diff_vs_mix"]
    better_sharpe = s["sharpe"] > h["sharpe"] and s["sharpe"] > m["sharpe"]
    smaller_dd = dd["value"] <= 2 / 3 and s["max_drawdown"] > m["max_drawdown"]
    if better_sharpe and smaller_dd and (sharpe_mix["low"] or 0) > 0:
        return "support"
    if s["sharpe"] <= m["sharpe"] or dd["value"] > 0.85:
        return "rejection"
    return "partial"


def evaluate(sim: h006.Simulation, window: tuple[date, date]) -> dict[str, Any] | None:
    """H006's evaluation plus the Sharpe differences the claim is about."""
    result = h006.evaluate(sim, window)
    if result is None:
        return None
    idx = h006._slice(sim, window)
    days = [sim.days[idx[0]], *(sim.days[k + 1] for k in idx)]
    strat = [sim.strategy[k] for k in idx]
    hold = [sim.hold[k] for k in idx]
    cash = [sim.cash[k] for k in idx]
    mix = h006.constant_mix(days, hold, cash, result["exposure"])
    rf = h006.monthly(days, cash)
    triples = list(
        zip(
            h006.monthly(days, strat),
            h006.monthly(days, hold),
            h006.monthly(days, mix),
            strict=True,
        )
    )

    def sharpe(xs: list[float]) -> float:
        sd = float(np.std(xs, ddof=1)) if len(xs) > 2 else 0.0
        return float(np.mean(xs)) / sd * math.sqrt(12) if sd else math.nan

    def diff(j: int):
        def stat(rows: list[tuple[tuple[float, float, float], float]]) -> float:
            return sharpe([t[0] - f for t, f in rows]) - sharpe([t[j] - f for t, f in rows])

        est = research.block_bootstrap(list(zip(triples, rf, strict=True)), stat, h006.BLOCK_MONTHS)
        return {"value": est.value, "low": est.low, "high": est.high}

    result["sharpe_diff_vs_hold"] = diff(1)
    result["sharpe_diff_vs_mix"] = diff(2)
    result["verdict"] = verdict(result)
    return result


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in PERIODS:
        raise ValueError(f"period is one of {tuple(PERIODS)}")
    if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, FROZEN):
        raise research.HoldoutError("freeze the variant first (study H009 --freeze), once")
    window = PERIODS[period]
    end = research.HOLDOUT_START if period != "holdout" else date.today()
    days, series = h006.load(session, end)
    if period != "holdout":
        research.check_period(period, days[-1])
    variants = [(WINDOW, TARGET)] if period == "holdout" else [(WINDOW, TARGET), *ROBUSTNESS]
    results = []
    for win, target in variants:
        variant = FROZEN | {"target": target, "window": win}
        assets: dict[str, Any] = {}
        for asset in h006.ASSETS:
            sim = simulate(days, series[f"px:{asset}"], series[h006.TBILL], target, win)
            result = evaluate(sim, window)
            if result is not None and period != "holdout":
                result["expanding"] = h006.expanding_view(sim, window)
            assets[asset] = result
        results.append({"period": period, "target": target, "window": win, "assets": assets})
        if log:
            research.log(session, HYPOTHESIS, variant, period, {"assets": assets})
    return results
