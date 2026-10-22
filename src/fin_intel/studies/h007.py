"""H007: selling index puts pays more when implied volatility exceeds realized
(docs/research/H007-variance-premium.md).

At each month's last close: VRP = VIX minus the annualized volatility of SPY's last 21
daily log returns; the slope = VIX3M minus VIX. Outcomes over the next month: the PUT
index's return over T-bills (the premium harvested), PUT minus SPY, SPY over T-bills.
Six fixed tests: the unconditional premium, a Newey-West regression on VRP (decisive),
expanding-window tercile sorts, a rule that writes puts only above the expanding median
(decisive), the same regression for equities, and the term-structure variants.

Cboe's PUT file is daily only from 2007 (seven stray values before), so the decisive tests
run on PUT from 2007 and the same tests on BXMD (30-delta covered calls, daily since 1986)
give the long-history reading, descriptive (amendment of 2026-10-22).
"""

import math
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from fin_intel import indicators, research, timeseries
from fin_intel.studies import h002

HYPOTHESIS = "H007"
VIX, VIX3M, PUT, SPY, TBILL = "cboe:vix", "cboe:vix3m", "cboe:put", "px:SPY", "fred:DGS3MO"
BXMD = "cboe:bxmd"  # the long-history instrument, descriptive
LONG_START = date(1993, 2, 1)
RV_DAYS = 21
MIN_HISTORY = 60  # months before the first sort or median
BLOCK_MONTHS = 24
ALPHA = 0.025  # two decisive tests
# Decision month ends; a month's outcome ends at the next month end.
PERIODS = {
    "design": (date(2007, 1, 1), date(2015, 12, 31)),  # PUT is daily from 2007
    "validation": (date(2016, 1, 1), date(2023, 5, 31)),
    "holdout": (date(2023, 6, 1), date(2100, 1, 1)),
}
FROZEN = {"signal": "vrp", "realized_days": RV_DAYS, "rule": "above expanding median"}


@dataclass
class Month:
    end: date
    vrp: float | None
    slope: float | None
    put_ret: float
    spy_ret: float
    rf: float

    @property
    def put_excess(self) -> float:
        return self.put_ret - self.rf

    @property
    def spy_excess(self) -> float:
        return self.spy_ret - self.rf

    @property
    def put_minus_spy(self) -> float:
        return self.put_ret - self.spy_ret


def realized_vol(prices: indicators.Series, i: int, days: int = RV_DAYS) -> float | None:
    """Annualized standard deviation of the `days` daily log returns ending at i, in VIX's
    units (percentage points)."""
    window = prices[i - days : i + 1]
    if len(window) < days + 1 or any(p is None or p <= 0 for p in window):
        return None
    logs = np.diff(np.log(np.asarray(window, dtype=float)))
    return float(np.std(logs, ddof=1) * math.sqrt(252) * 100)


def months_from(
    days: list[date], series: dict[str, indicators.Series], instrument: str = PUT
) -> list[Month]:
    """One row per month end with every input: `put_ret` is the instrument's return."""
    ends = h002.month_ends(days)
    out = []
    for a, b in zip(ends, ends[1:], strict=False):
        vix, put0, put1, spy0, spy1 = (
            series[VIX][a],
            series[instrument][a],
            series[instrument][b],
            series[SPY][a],
            series[SPY][b],
        )
        rf = series[TBILL][a]
        if None in (vix, put0, put1, spy0, spy1, rf):
            continue
        rv = realized_vol(series[SPY], a)
        vix3m = series[VIX3M][a] if VIX3M in series else None
        out.append(
            Month(
                end=days[a],
                vrp=vix - rv if rv is not None else None,
                slope=vix3m - vix if vix3m is not None else None,
                put_ret=put1 / put0 - 1,
                spy_ret=spy1 / spy0 - 1,
                rf=rf / 100 / 12,
            )
        )
    return out


def expanding_rank(values: list[float | None], k: int) -> tuple[float | None, float | None]:
    """The percentile of values[k] in values[:k] and that history's median, once
    MIN_HISTORY months exist."""
    history = [v for v in values[:k] if v is not None]
    v = values[k]
    if v is None or len(history) < MIN_HISTORY:
        return None, None
    return sum(1 for h in history if h < v) / len(history), median(history)


# --- statistics ---


def describe(returns: list[float], rf: list[float]) -> dict[str, Any]:
    excess = [r - f for r, f in zip(returns, rf, strict=True)]
    years = len(returns) / 12
    total = math.prod(1 + r for r in returns)
    sd = float(np.std(excess, ddof=1)) if len(excess) > 2 else math.nan
    peak, worst, e = 1.0, 0.0, 1.0
    for r in returns:
        e *= 1 + r
        peak = max(peak, e)
        worst = min(worst, e / peak - 1)
    return {
        "cagr": total ** (1 / years) - 1 if years and total > 0 else math.nan,
        "volatility": float(np.std(returns, ddof=1)) * math.sqrt(12)
        if len(returns) > 2
        else math.nan,
        "sharpe": float(np.mean(excess)) / sd * math.sqrt(12) if sd else math.nan,
        "max_drawdown": worst,
        "months": len(returns),
    }


def sharpe(excess: list[float]) -> float:
    sd = float(np.std(excess, ddof=1)) if len(excess) > 2 else 0.0
    return float(np.mean(excess)) / sd * math.sqrt(12) if sd else math.nan


def regression(outcome: list[float], signal: list[float | None]) -> dict[str, Any]:
    est = research.hac_slope(outcome, signal, lags=1)
    p = research.one_sided_p(est)
    return {"slope": est.value, "se": est.se, "t": est.t, "p": p, "n": est.n,
            "verdict": "support" if p is not None and p < ALPHA and est.value > 0 else
            "no" if p is None or p >= 0.1 or est.value <= 0 else "weak"}  # fmt: skip


def sorts(outcome: list[float], ranks: list[float | None]) -> dict[str, dict[str, float | None]]:
    out = {}
    for name, lo, hi in (("bottom", 0.0, 1 / 3), ("middle", 1 / 3, 2 / 3), ("top", 2 / 3, 1.01)):
        rows = [o for o, r in zip(outcome, ranks, strict=True) if r is not None and lo <= r < hi]
        est = research.newey_west_mean(rows, lags=1)
        out[name] = {"mean": est.value, "low": est.low, "high": est.high, "months": est.n}
    return out


def rule_test(months: list[Month], on: list[bool | None], reps: int = 2000) -> dict[str, Any]:
    """Hold PUT when `on`, T-bills otherwise, against always-PUT and SPY; the Sharpe
    difference against always-PUT with a block-bootstrap interval."""
    rows = [(m, flag) for m, flag in zip(months, on, strict=True) if flag is not None]
    if len(rows) < 24:
        return {"months": len(rows)}
    rf = [m.rf for m, _ in rows]
    rule = [m.put_ret if flag else m.rf for m, flag in rows]
    put = [m.put_ret for m, _ in rows]
    spy = [m.spy_ret for m, _ in rows]
    pairs = [(r - f, p - f) for r, p, f in zip(rule, put, rf, strict=True)]
    diff = research.block_bootstrap(
        pairs, lambda xs: sharpe([a for a, _ in xs]) - sharpe([b for _, b in xs]),
        BLOCK_MONTHS, reps=reps,
    )  # fmt: skip
    return {
        "rule": describe(rule, rf),
        "always_put": describe(put, rf),
        "spy": describe(spy, rf),
        "share_on": sum(1 for _, flag in rows if flag) / len(rows),
        "sharpe_difference": {"value": diff.value, "low": diff.low, "high": diff.high},
        "verdict": "support"
        if diff.low is not None and diff.low > 0
        else "no"
        if diff.value <= 0
        else "weak",  # fmt: skip
    }


def analyze(all_months: list[Month], window: tuple[date, date], reps: int = 2000) -> dict[str, Any]:
    """The six tests on the months whose decision date falls in `window`; sorts and medians
    use the expanding history before each month, from the start of the data."""
    vrp_all = [m.vrp for m in all_months]
    slope_all = [m.slope for m in all_months]
    idx = [k for k, m in enumerate(all_months) if window[0] <= m.end <= window[1]]
    months = [all_months[k] for k in idx]
    if len(months) < 24:
        return {"months": len(months)}
    put_excess = [m.put_excess for m in months]
    spy_excess = [m.spy_excess for m in months]
    rf = [m.rf for m in months]
    vrp = [m.vrp for m in months]
    vrp_rank, vrp_median = zip(*(expanding_rank(vrp_all, k) for k in idx), strict=True)
    slope_rank, slope_median = zip(*(expanding_rank(slope_all, k) for k in idx), strict=True)

    def above(values, medians) -> list[bool | None]:
        return [None if v is None or med is None else v > med
                for v, med in zip(values, medians, strict=True)]  # fmt: skip

    unconditional = research.newey_west_mean(put_excess, lags=1)
    minus_spy = research.newey_west_mean([m.put_minus_spy for m in months], lags=1)
    return {
        "months": len(months),
        "start": months[0].end,
        "end": months[-1].end,
        "unconditional": {
            "put_excess_mean": unconditional.value,
            "t": unconditional.t,
            "put": describe([m.put_ret for m in months], rf),
            "spy": describe([m.spy_ret for m in months], rf),
            "put_minus_spy_mean": minus_spy.value,
            "put_minus_spy_t": minus_spy.t,
        },  # fmt: skip
        "regression_vrp": regression(put_excess, vrp),
        "sorts_vrp": sorts(put_excess, list(vrp_rank)),
        "rule_vrp": rule_test(months, above(vrp, vrp_median), reps),
        "regression_vrp_equities": regression(spy_excess, vrp),
        "term_structure": {
            "regression": regression(put_excess, [m.slope for m in months]),
            "sorts": sorts(put_excess, list(slope_rank)),
            "rule": rule_test(months, above([m.slope for m in months], slope_median), reps),
        },
    }


def verdict(result: dict[str, Any]) -> str:
    reg, rule = result.get("regression_vrp"), result.get("rule_vrp")
    if not reg or not rule or "verdict" not in rule:
        return "too few months"
    if reg["verdict"] == "support" and rule["verdict"] == "support":
        return "support"
    if reg["verdict"] == "no" or rule["verdict"] == "no":
        premium = result["unconditional"]
        return "weak support (premium only)" if (premium["t"] or 0) >= 2 else "rejection"
    return "partial"


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in PERIODS:
        raise ValueError(f"period is one of {tuple(PERIODS)}")
    if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, FROZEN):
        raise research.HoldoutError("freeze the variant first (study H007 --freeze), once")
    end = research.HOLDOUT_START if period != "holdout" else date.today()
    specs = [VIX, VIX3M, PUT, BXMD, SPY, TBILL]
    days, series = timeseries.build(session, specs, None, end, pit=False)
    series[TBILL] = [None, *series[TBILL][:-1]]  # posted the next morning
    months = months_from(days, series)
    if period != "holdout":
        last_outcome = max((days[b] for b in h002.month_ends(days)), default=days[-1])
        research.check_period(period, last_outcome)
    window = PERIODS[period]
    result = analyze(months, window)
    result["period"] = period
    result["verdict"] = verdict(result)
    # The long-history reading on covered calls, from 1993 through the period's end
    # (descriptive: a different instrument, and months the decisive tests also use).
    long_window = (LONG_START if period != "holdout" else window[0], window[1])
    result["long_history_bxmd"] = analyze(months_from(days, series, BXMD), long_window)
    if log:
        research.log(session, HYPOTHESIS, FROZEN, period, result)
    return [result]
