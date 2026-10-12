"""H002: iron tracks present industrial activity, copper anticipates it
(docs/research/H002-iron-present-copper-future.md).

Predictors are pseudo-real-time: ALFRED vintages for the metal and steel series begin
only in 2015, so each predictor is its final value lagged by its publication delay (a
month's figure comes out early the next month: at a month end, the previous month is
known). Metal prices are effectively never revised; steel output is, a little.
Industrial production's own history, a predictor in the baseline, is truly point in time
(vintages since 1927). The target is what happened (final industrial production). Two
tests per predictor: the lead-lag peak against industrial production's year-on-year
growth, and whether the predictor improves an out-of-sample forecast of
industrial production over its own recent growth.
"""

import math
from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
from sqlalchemy.orm import Session

from fin_intel import research, timeseries

HYPOTHESIS = "H002"
PREDICTORS = {
    "copper": "fred:PCOPPUSDM",
    "iron_ore": "fred:PIORECRUSDM",
    "steel_output": "fred:IPG3311A2S",
    "steel_price": "fred:WPU1017",
}
TARGET = "fred:INDPRO"
# (design start, design end, validation end) per predictor; iron ore was a benchmark-
# contract price until 2010, so its monthly values carry no monthly information before.
PERIODS = {
    "copper": (date(1990, 1, 1), date(2012, 12, 31), date(2023, 6, 30)),
    "steel_output": (date(1990, 1, 1), date(2012, 12, 31), date(2023, 6, 30)),
    "steel_price": (date(1990, 1, 1), date(2012, 12, 31), date(2023, 6, 30)),
    "iron_ore": (date(2010, 1, 1), date(2017, 12, 31), date(2023, 6, 30)),
}
LAGS = range(-12, 13)
MIN_FIT = 60  # months of history before the first out-of-sample forecast


@dataclass
class Result:
    predictor: str
    period: str
    transform: str
    horizon: int
    months: int
    peak_lag: int | None
    peak_corr: float | None
    peak_low: float | None
    peak_high: float | None
    corr_at_0: float | None
    oos_r2: float | None
    clark_west_t: float | None
    clark_west_p: float | None
    forecasts: int


def log_growth(values: list[float | None], months: int = 12) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    for t in range(months, len(values)):
        a, b = values[t], values[t - months]
        if a and b and a > 0 and b > 0:
            out[t] = math.log(a / b)
    return out


def acceleration(growth: list[float | None], months: int = 3) -> list[float | None]:
    out: list[float | None] = [None] * len(growth)
    for t in range(months, len(growth)):
        if growth[t] is not None and growth[t - months] is not None:
            out[t] = growth[t] - growth[t - months]
    return out


def _peak(x: list, y: list) -> tuple[int | None, float | None]:
    corr = research.cross_correlation(x, y, LAGS)
    if not corr:
        return None, None
    lag = max(corr, key=lambda k: corr[k])
    return lag, corr[lag]


def _ols_forecast(rows: list[tuple[list[float], float]], x_now: list[float]) -> float:
    X = np.asarray([[1.0, *r[0]] for r in rows])
    y = np.asarray([r[1] for r in rows])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    return float(np.dot([1.0, *x_now], beta))


def analyze(
    months: list[date],
    predictor: list[float | None],
    ip_known: list[float | None],
    ip_final: list[float | None],
    window: tuple[date, date],
    transform: str,
    horizon: int,
    name: str = "",
    period: str = "",
    reps: int = 500,
    skip: tuple[date, date] | None = None,
) -> Result:
    """`months` are month ends; `predictor` and `ip_known` are values as known then,
    `ip_final` what industrial production turned out to be. Statistics use months inside
    `window` (forecast fits may use earlier months); forecasts made inside `skip` aren't
    scored."""
    grow = log_growth(predictor)
    signal = grow if transform == "growth" else acceleration(grow)
    ip_yoy_final = log_growth(ip_final)
    ip_yoy_known = log_growth(ip_known)
    inside = [i for i, m in enumerate(months) if window[0] <= m <= window[1]]

    # 1. Lead-lag against what industrial production did.
    x = [signal[i] for i in inside]
    y = [ip_yoy_final[i] for i in inside]
    lag, corr = _peak(x, y)
    interval = research.block_bootstrap(
        list(zip(x, y, strict=True)),
        lambda pairs: _peak(*map(list, zip(*pairs, strict=True)))[0] or 0,
        block=24,
        reps=reps,
    )
    at_zero = research.cross_correlation(x, y, range(0, 1)).get(0)

    # 2. Out-of-sample forecast of the next `horizon` months of industrial production.
    target = [None] * len(months)
    for t in range(len(months) - horizon):
        a, b = ip_final[t + horizon], ip_final[t]
        if a and b:
            target[t] = math.log(a / b)
    actual, base_f, model_f = [], [], []
    for t in inside:
        if target[t] is None or ip_yoy_known[t] is None or signal[t] is None:
            continue
        if skip and skip[0] <= months[t] <= skip[1]:
            continue
        # Fit only on months whose outcome was known by month t.
        history = [
            s
            for s in range(t - horizon)
            if target[s] is not None and ip_yoy_known[s] is not None and signal[s] is not None
        ]
        if len(history) < MIN_FIT:
            continue
        base_rows = [([ip_yoy_known[s]], target[s]) for s in history]
        model_rows = [([ip_yoy_known[s], signal[s]], target[s]) for s in history]
        actual.append(target[t])
        base_f.append(_ols_forecast(base_rows, [ip_yoy_known[t]]))
        model_f.append(_ols_forecast(model_rows, [ip_yoy_known[t], signal[t]]))
    if len(actual) >= 24:
        oos = research.out_of_sample_r2(actual, base_f, model_f)
        cw_t, cw_p = research.clark_west(actual, base_f, model_f, lags=horizon - 1)
    else:
        oos = cw_t = cw_p = None
    return Result(
        predictor=name,
        period=period,
        transform=transform,
        horizon=horizon,
        months=len(inside),
        peak_lag=lag,
        peak_corr=corr,
        peak_low=interval.low,
        peak_high=interval.high,
        corr_at_0=at_zero,
        oos_r2=oos,
        clark_west_t=cw_t,
        clark_west_p=cw_p,
        forecasts=len(actual),
    )


def month_ends(days: list[date]) -> list[int]:
    """Indexes of each month's last trading day."""
    return [i for i in range(len(days)) if i + 1 == len(days) or days[i + 1].month != days[i].month]


def run(session: Session, period: str, log: bool = True) -> list[Result]:
    if period not in ("design", "validation"):
        raise ValueError("H002 runs design or validation here; the holdout needs a frozen variant")
    days, known = timeseries.build(session, [TARGET], None, research.HOLDOUT_START, pit=True)
    _, final = timeseries.build(
        session, [*PREDICTORS.values(), TARGET], None, research.HOLDOUT_START, pit=False
    )
    ends = [i for i in month_ends(days) if days[i] < research.HOLDOUT_START]
    months = [days[i] for i in ends]
    research.check_period(period, months[-1])
    ip_known = [known[TARGET][i] for i in ends]
    ip_final = [final[TARGET][i] for i in ends]
    results = []
    for name, spec in PREDICTORS.items():
        start, design_end, validation_end = PERIODS[name]
        window = (start, design_end) if period == "design" else (design_end, validation_end)
        monthly = [final[spec][i] for i in ends]
        values = [None, *monthly[:-1]]  # last month's figure, the one published by now
        for transform in ("growth", "acceleration"):
            for horizon in (3, 6, 12):
                result = analyze(
                    months, values, ip_known, ip_final, window, transform, horizon, name, period
                )
                results.append(result)
                if log:
                    variant = {"predictor": name, "transform": transform, "horizon": horizon}
                    research.log(session, HYPOTHESIS, variant, period, asdict(result))
    return results
