"""H005: what copper measures (docs/research/H005-what-copper-measures.md).

Seven fixed tests in three claims: (a) China's credit impulse leads copper (a1, the lead
in final values) and forecasts it as known (a2); (b) copper moves with China's industry
more than the United States' (b1) and nowcasts US and OECD industrial production (b2, b3);
(c) the gap between the 10-year yield and what copper/gold implies closes through the
yield (c1) or through the ratio (c2).

Series are read straight from the database by month (the timeseries calendar starts with
the stock data in 1993; these tests go back to 1976). Copper and gold are World Bank
monthly averages, public 5 days after the month; China's credit impulse is public 170 days
after its quarter; US industrial production is as known from ALFRED vintages; OECD's is
final values lagged two months.
"""

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import commodities, credit, research
from fin_intel.models import EconomicObservation, EconomicVintage

HYPOTHESIS = "H005"
FROZEN = {"tests": "a1 a2 b1 b2 b3 c1 c2", "registered": "2026-10-12"}
ALPHA = 0.05 / 7
PANDEMIC = (date(2020, 3, 1), date(2021, 6, 1))
MIN_FIT_MONTHS = 60
MIN_FIT_QUARTERS = 40
RATIO_START = date(1976, 1, 1)  # gold floats freely from 1975

# Forecast origins (months, or quarter-end months for (a)) per test group and period.
PERIODS = {
    "a": {
        "design": (date(1990, 1, 1), date(2012, 12, 31)),
        "validation": (date(2013, 1, 1), date(2022, 12, 31)),
        "holdout": (date(2023, 7, 1), date(2100, 1, 1)),
    },
    "b1": {
        "design": (date(1999, 1, 1), date(2012, 12, 31)),
        "validation": (date(2013, 1, 1), date(2023, 6, 30)),
    },
    "b": {
        "design": (date(1980, 1, 1), date(2012, 12, 31)),
        "validation": (date(2013, 1, 1), date(2023, 6, 30)),
        "holdout": (date(2023, 7, 1), date(2100, 1, 1)),
    },
    "c": {
        "design": (date(1981, 1, 1), date(2005, 12, 31)),
        "validation": (date(2006, 1, 1), date(2023, 2, 28)),
        "holdout": (date(2023, 7, 1), date(2100, 1, 1)),
    },
}


# --- months ---


def month(d: date) -> date:
    return date(d.year, d.month, 1)


def shift(m: date, months: int) -> date:
    k = m.year * 12 + m.month - 1 + months
    return date(k // 12, k % 12 + 1, 1)


def _monthly(session: Session, series_id: str) -> dict[date, float]:
    """Monthly values (daily series averaged by month), final."""
    sums: dict[date, list[float]] = defaultdict(list)
    for d, v in session.execute(
        select(EconomicObservation.date, EconomicObservation.value).where(
            EconomicObservation.series_id == series_id, EconomicObservation.value.is_not(None)
        )
    ):
        sums[month(d)].append(v)
    return {m: sum(vs) / len(vs) for m, vs in sums.items()}


def yoy(levels: dict[date, float]) -> dict[date, float]:
    return {
        m: math.log(v / levels[shift(m, -12)])
        for m, v in levels.items()
        if v > 0 and levels.get(shift(m, -12), 0) > 0
    }


def known_yoy(rows: list[tuple[date, date, float | None]], days: list[date]) -> list:
    """Year-on-year log growth of the latest month known on each day, both months as
    published by then: rows are (realtime_start, observation month, value) in order."""
    known: dict[date, float] = {}
    out, i = [], 0
    for d in days:
        while i < len(rows) and rows[i][0] <= d:
            _, m, v = rows[i]
            if v is None:
                known.pop(m, None)
            else:
                known[m] = v
            i += 1
        latest = max(known, default=None)
        prior = known.get(shift(latest, -12)) if latest else None
        out.append(math.log(known[latest] / prior) if latest and prior else None)
    return out


# --- forecasting ---


@dataclass
class Origin:
    when: date
    base: list[float]  # baseline features (empty: the historical mean)
    extra: list[float]  # the model's additional features
    target: float
    ready: date  # first origin at which the target is known


def forecast(origins: list[Origin], window: tuple[date, date], zero_base: bool = False,
             min_fit: int = MIN_FIT_MONTHS, lags: int = 0,
             skip: tuple[date, date] | None = None) -> dict[str, Any]:  # fmt: skip
    """Expanding-window out-of-sample forecasts at origins inside `window`, each fitted on
    earlier origins whose targets were known by then. The baseline is a regression on
    `base` (the historical mean when empty) or zero; the model adds `extra`."""
    actual, base_f, model_f, slopes = [], [], [], []
    for o in origins:
        if not window[0] <= o.when <= window[1] or (skip and skip[0] <= o.when <= skip[1]):
            continue
        history = [h for h in origins if h.ready <= o.when and h.when < o.when]
        if len(history) < min_fit:
            continue
        y = np.asarray([h.target for h in history])
        Xm = np.asarray([[1.0, *h.base, *h.extra] for h in history])
        beta, *_ = np.linalg.lstsq(Xm, y, rcond=None)
        model_f.append(float(np.dot([1.0, *o.base, *o.extra], beta)))
        slopes.append(float(beta[-1]))
        if zero_base:
            base_f.append(0.0)
        else:
            Xb = np.asarray([[1.0, *h.base] for h in history])
            gamma, *_ = np.linalg.lstsq(Xb, y, rcond=None)
            base_f.append(float(np.dot([1.0, *o.base], gamma)))
        actual.append(o.target)
    if len(actual) < 24:
        return {"forecasts": len(actual), "oos_r2": None, "clark_west_t": None,
                "clark_west_p": None, "mean_slope": None}  # fmt: skip
    t, p = research.clark_west(actual, base_f, model_f, lags=lags)
    return {
        "forecasts": len(actual),
        "first": min(o.when for o in origins if window[0] <= o.when <= window[1]),
        "oos_r2": research.out_of_sample_r2(actual, base_f, model_f),
        "clark_west_t": t,
        "clark_west_p": p,
        "mean_slope": float(np.mean(slopes)),
    }


def passes(result: dict[str, Any], sign: int = 0) -> bool:
    if result.get("oos_r2") is None:
        return False
    ok = result["oos_r2"] > 0 and result["clark_west_p"] < ALPHA
    return ok and (sign == 0 or math.copysign(1, result["mean_slope"]) == sign)


# --- (a) China's credit and copper ---


def quarter_end(m: date) -> date:
    q = (m.month - 1) // 3
    return shift(date(m.year, q * 3 + 1, 1), 3) - timedelta(days=1)


def quarterly_copper(copper: dict[date, float]) -> dict[date, float]:
    by_q: dict[date, list[float]] = defaultdict(list)
    for m, v in copper.items():
        by_q[quarter_end(m)].append(v)
    return {q: sum(vs) / 3 for q, vs in by_q.items() if len(vs) == 3}


def test_a(impulse: list[tuple[date, float]], copper: dict[date, float],
           window: tuple[date, date], reps: int = 1000) -> dict[str, Any]:  # fmt: skip
    cu = quarterly_copper(copper)
    quarters = sorted(cu)
    index = {q: i for i, q in enumerate(quarters)}
    growth = {q: math.log(cu[q] / cu[quarters[index[q] - 4]]) for q in quarters[4:]}
    imp = dict(impulse)

    # a1: the impulse at t against copper's growth at t + lead, origins in the window.
    inside = [q for q in quarters if window[0] <= q <= window[1]]
    x = [imp.get(q) for q in inside]

    def ahead(q: date, k: int) -> float | None:
        """Copper's growth k quarters later, only if it falls inside the window."""
        j = index[q] + k
        return growth.get(quarters[j]) if j < len(quarters) and quarters[j] <= window[1] else None

    after = {q: [ahead(q, k) for k in range(7)] for q in inside}

    def peak(rows: list[tuple[float | None, list]]) -> float:
        corr = {}
        for k in range(7):
            pairs = [(a, b[k]) for a, b in rows if a is not None and b[k] is not None]
            if len(pairs) > 10:
                u, v = np.asarray(pairs, dtype=float).T
                corr[k] = float(np.corrcoef(u, v)[0, 1])
        return float(max(corr, key=lambda k: corr[k])) if corr else math.nan

    rows = list(zip(x, [after[q] for q in inside], strict=True))
    lead = research.block_bootstrap(rows, peak, block=8, reps=reps)
    by_lead = {}
    for k in range(7):
        pairs = [(a, b[k]) for a, b in rows if a is not None and b[k] is not None]
        if len(pairs) > 10:
            u, v = np.asarray(pairs, dtype=float).T
            by_lead[k] = round(float(np.corrcoef(u, v)[0, 1]), 3)
    a1 = {"peak_lead": lead.value, "lead_low": lead.low, "lead_high": lead.high,
          "corr_by_lead": by_lead, "quarters": len(inside)}  # fmt: skip
    a1["passes"] = bool(lead.value >= 2 and lead.low is not None and lead.low > 0)

    # a2: copper's change over the next two quarters from the impulse as known.
    origins = []
    for i, q in enumerate(quarters[:-2]):
        known = [v for iq, v in impulse if iq + credit.PUBLICATION_LAG <= q]
        if not known:
            continue
        origins.append(Origin(q, [], [known[-1]], math.log(cu[quarters[i + 2]] / cu[q]),
                              ready=quarters[i + 2]))  # fmt: skip
    a2 = forecast(origins, window, min_fit=MIN_FIT_QUARTERS, lags=1)
    a2["passes"] = passes(a2)
    return {"a1": a1, "a2": a2}


# --- (b) whose activity, and the nowcast ---


def test_b1(copper_g: dict[date, float], china: dict[date, float], us_g: dict[date, float],
            window: tuple[date, date], reps: int = 1000) -> dict[str, Any]:  # fmt: skip
    rows = [
        (copper_g[m], china[m], us_g[m])
        for m in sorted(copper_g)
        if window[0] <= m <= window[1] and m in china and m in us_g
    ]

    def difference(rs: list[tuple[float, float, float]]) -> float:
        a = np.asarray(rs, dtype=float)
        return float(np.corrcoef(a[:, 0], a[:, 1])[0, 1] - np.corrcoef(a[:, 0], a[:, 2])[0, 1])

    if len(rows) < 48:
        return {"months": len(rows), "passes": False, "skipped": "too few months"}
    est = research.block_bootstrap(rows, difference, block=24, reps=reps)
    a = np.asarray(rows, dtype=float)
    out = {
        "months": len(rows),
        "corr_china": float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]),
        "corr_us": float(np.corrcoef(a[:, 0], a[:, 2])[0, 1]),
        "difference": est.value,
        "low": est.low,
        "high": est.high,
    }
    out["passes"] = bool(est.value > 0 and est.low is not None and est.low > 0)
    return out


def nowcast_origins(copper_g: dict[date, float], target: dict[date, float],
                    known_base: Callable[[date], float | None],
                    known_lag: int) -> list[Origin]:  # fmt: skip
    """Origin m: this month's growth (target), nowcast 5 days after the month from the
    baseline's latest known figure and copper's growth over the month. A month's target
    counts as known `known_lag` months after it (its first publication)."""
    out = []
    for m in sorted(target):
        base = known_base(m)
        if base is None or m not in copper_g:
            continue
        out.append(Origin(m, [base], [copper_g[m]], target[m], ready=shift(m, known_lag)))
    return out


# --- (c) copper/gold and the 10-year yield ---


def gap_origins(ratio: dict[date, float], yields: dict[date, float]) -> tuple[list, list]:
    """(c1 origins, c2 origins): at month m, the gap between the yield and its fitted
    value from log(copper/gold) on months up to m; targets from m+1 to m+4."""
    months = sorted(m for m in ratio if m in yields and m >= RATIO_START)
    c1, c2 = [], []
    for i, m in enumerate(months):
        if i + 1 < MIN_FIT_MONTHS:
            continue
        x = np.asarray([ratio[k] for k in months[: i + 1]])
        y = np.asarray([yields[k] for k in months[: i + 1]])
        b, a = np.polyfit(x, y, 1)
        gap = yields[m] - (a + b * ratio[m])
        start, end = shift(m, 1), shift(m, 4)
        if start in yields and end in yields and start in ratio and end in ratio:
            c1.append(Origin(m, [], [gap], yields[end] - yields[start], ready=end))
            c2.append(Origin(m, [], [gap], ratio[end] - ratio[start], ready=end))
    return c1, c2


# --- the run ---


def _vintages(session: Session, series_id: str) -> list[tuple[date, date, float | None]]:
    return [
        (r, d, v)
        for r, d, v in session.execute(
            select(EconomicVintage.realtime_start, EconomicVintage.date, EconomicVintage.value)
            .where(EconomicVintage.series_id == series_id)
            .order_by(EconomicVintage.realtime_start, EconomicVintage.date)
        )
    ]


def evaluate(session: Session, period: str) -> dict[str, Any]:
    copper = _monthly(session, "WB_COPPER")
    gold = _monthly(session, "WB_GOLD")
    copper_g = yoy(copper)
    us_final = yoy(_monthly(session, "INDPRO"))
    china = {m: v - 100 for m, v in _monthly(session, "CHNPRINTO01IXPYM").items()}
    oecd = _monthly(session, "OECDPRINTO01GYSAM")
    yields = _monthly(session, "DGS10")
    ratio = {m: math.log(copper[m] / gold[m]) for m in copper if gold.get(m)}
    impulse = credit.history(session, "CN", "credit_impulse")

    out: dict[str, Any] = {"period": period}
    if period in PERIODS["a"]:
        out.update(test_a(impulse, copper, PERIODS["a"][period]))
    if period in PERIODS["b1"]:
        out["b1"] = test_b1(copper_g, china, us_final, PERIODS["b1"][period])

    # The nowcast is made when the month's copper average is public.
    us_rows = _vintages(session, "INDPRO")
    us_months = sorted(us_final)
    us_known = dict(
        zip(us_months, known_yoy(us_rows, [commodities.available_on(m) for m in us_months]),
            strict=True)
    )  # fmt: skip
    b2_origins = nowcast_origins(copper_g, us_final, us_known.get, known_lag=1)
    b3_origins = nowcast_origins(copper_g, oecd, lambda m: oecd.get(shift(m, -2)), known_lag=2)
    c1_origins, c2_origins = gap_origins(ratio, yields)
    window_b, window_c = PERIODS["b"][period], PERIODS["c"][period]
    tests = {
        "b2": (b2_origins, window_b, False, 0, 0),
        "b3": (b3_origins, window_b, False, 0, 0),
        "c1": (c1_origins, window_c, True, 2, -1),
        "c2": (c2_origins, window_c, True, 2, 1),
    }
    for name, (origins, window, zero, lags, sign) in tests.items():
        result = forecast(origins, window, zero_base=zero, lags=lags)
        result["passes"] = passes(result, sign)
        out[name] = result
        if period == "validation":
            out[f"{name}_without_pandemic"] = forecast(
                origins, window, zero_base=zero, lags=lags, skip=PANDEMIC
            )
    return out


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    """Design, validation and the holdout each run once on the fixed tests; the holdout
    needs the variant frozen first."""
    if period not in research.PERIODS:
        raise ValueError(f"period is one of {research.PERIODS}")
    if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, FROZEN):
        raise research.HoldoutError("freeze first (study H005 --freeze), once")
    result = evaluate(session, period)
    if log:
        research.log(session, HYPOTHESIS, FROZEN, period, result)
    return [result]
