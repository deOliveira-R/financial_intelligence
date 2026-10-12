"""H004: copper's acceleration anticipates industrial production by a quarter
(docs/research/H004-copper-acceleration.md).

One variant, fixed in the pre-registration: copper's acceleration (pseudo-real-time, as in
H002) added to industrial production's own growth, forecasting the next three months of
industrial production (H002's `analyze`, unchanged); and, secondary, the same signal
forecasting cyclical against defensive sectors over three months, against the historical
mean. Validation (2013-01 to 2023-03 forecasts) and the holdout (from 2023-07) are both
months H002 never examined for this variant.
"""

import math
from dataclasses import asdict
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from fin_intel import research, timeseries
from fin_intel.studies import h002

HYPOTHESIS = "H004"
COPPER = "fred:PCOPPUSDM"
CYCLICAL = ("px:XLI", "px:XLB")
DEFENSIVE = ("px:XLU", "px:XLP")
HORIZON = 3
FIT_START = date(1990, 1, 1)
SECTORS_START = date(1999, 1, 1)
WINDOWS = {
    "validation": (date(2013, 1, 1), date(2023, 3, 31)),
    "holdout": (research.HOLDOUT_START, date(2100, 1, 1)),
}
PANDEMIC = (date(2020, 3, 1), date(2021, 6, 30))
ALPHA = 0.025  # two tests
FROZEN = {"predictor": "copper", "transform": "acceleration", "horizon": HORIZON}


def sector_forecasts(
    months: list[date],
    signal: list[float | None],
    relative: list[float | None],
    window: tuple[date, date],
    skip: tuple[date, date] | None = None,
) -> dict[str, Any]:
    """Out-of-sample forecasts of the next `HORIZON` months' relative return: the
    historical mean (base) against a regression on the signal (model), refitted monthly on
    months whose outcome was known."""
    target: list[float | None] = [None] * len(months)
    for t in range(len(months) - HORIZON):
        if relative[t] is not None and relative[t + HORIZON] is not None:
            target[t] = relative[t + HORIZON] - relative[t]  # type: ignore[operator]
    actual, base_f, model_f = [], [], []
    for t, m in enumerate(months):
        if not window[0] <= m <= window[1] or target[t] is None or signal[t] is None:
            continue
        if skip and skip[0] <= m <= skip[1]:
            continue
        history = [s for s in range(t - HORIZON) if target[s] is not None and signal[s] is not None]
        if len(history) < h002.MIN_FIT:
            continue
        ys = np.asarray([target[s] for s in history])
        X = np.asarray([[1.0, signal[s]] for s in history])
        beta, *_ = np.linalg.lstsq(X, ys, rcond=None)
        actual.append(target[t])
        base_f.append(float(ys.mean()))
        model_f.append(float(beta[0] + beta[1] * signal[t]))
    if len(actual) < 24:
        return {"forecasts": len(actual), "oos_r2": None, "clark_west_t": None,
                "clark_west_p": None}  # fmt: skip
    t_stat, p = research.clark_west(actual, base_f, model_f, lags=HORIZON - 1)
    return {
        "forecasts": len(actual),
        "oos_r2": research.out_of_sample_r2(actual, base_f, model_f),
        "clark_west_t": t_stat,
        "clark_west_p": p,
    }


def verdict(test: dict[str, Any]) -> str:
    if test.get("oos_r2") is None:
        return "too few forecasts"
    if test["oos_r2"] > 0 and test["clark_west_p"] < ALPHA:
        return "significant gain"
    return "gain, not significant" if test["oos_r2"] > 0 else "no gain"


def _log_basket(final: dict[str, list], ends: list[int], tickers: tuple[str, ...]) -> list:
    """Average cumulative log price of a basket at each month end (adjusted closes)."""
    out = []
    for i in ends:
        values = [final[t][i] for t in tickers]
        out.append(
            sum(math.log(v) for v in values) / len(values)
            if all(v and v > 0 for v in values)
            else None
        )
    return out


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in WINDOWS:
        raise ValueError("H004 runs validation, then the holdout once (its design was H002's)")
    if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, FROZEN):
        raise research.HoldoutError("freeze the variant first (study H004 --freeze), once")
    end = research.HOLDOUT_START if period == "validation" else date.today()
    specs = [COPPER, h002.TARGET, *CYCLICAL, *DEFENSIVE]
    days, known = timeseries.build(session, [h002.TARGET], FIT_START, end, pit=True)
    _, final = timeseries.build(session, specs, FIT_START, end, pit=False)
    ends = [i for i in h002.month_ends(days) if days[i] < end]
    months = [days[i] for i in ends]
    if period == "validation":
        research.check_period(period, months[-1])
    ip_known = [known[h002.TARGET][i] for i in ends]
    ip_final = [final[h002.TARGET][i] for i in ends]
    monthly = [final[COPPER][i] for i in ends]
    copper = [None, *monthly[:-1]]  # last month's figure, the one published by now
    window = WINDOWS[period]

    primary = asdict(
        h002.analyze(months, copper, ip_known, ip_final, window, "acceleration", HORIZON,
                     "copper", period)
    )  # fmt: skip
    primary["verdict"] = verdict(primary)
    signal = h002.acceleration(h002.log_growth(copper))
    relative = [
        c - d if c is not None and d is not None and m >= SECTORS_START else None
        for c, d, m in zip(
            _log_basket(final, ends, CYCLICAL), _log_basket(final, ends, DEFENSIVE), months,
            strict=True,
        )
    ]  # fmt: skip
    secondary = sector_forecasts(months, signal, relative, window)
    secondary["verdict"] = verdict(secondary)
    result: dict[str, Any] = {"period": period, "primary": primary, "secondary": secondary}
    if period == "validation":
        # Robustness, not decisive: without the pandemic's collapse and rebound.
        without = h002.analyze(months, copper, ip_known, ip_final, window, "acceleration",
                               HORIZON, reps=50, skip=PANDEMIC)  # fmt: skip
        result["primary_without_pandemic"] = {
            k: getattr(without, k) for k in ("forecasts", "oos_r2", "clark_west_t", "clark_west_p")
        }
        result["secondary_without_pandemic"] = sector_forecasts(
            months, signal, relative, window, skip=PANDEMIC
        )
    if log:
        research.log(session, HYPOTHESIS, FROZEN, period, result)
    return [result]
