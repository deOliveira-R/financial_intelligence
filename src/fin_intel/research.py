"""The study harness for phase 5 (docs/research.md): the trial log, the holdout guard,
and statistics that respect how financial data misleads.

- Every evaluation is logged (`log`): hypothesis, variant, period, metrics, code version,
  so a result is read against the number of variants tried.
- The holdout (events from HOLDOUT_START) opens only for a frozen variant, once
  (`freeze`, `holdout_allowed`).
- Overlapping horizons and clustered events aren't independent: means get Newey-West
  errors, intervals come from block or cluster bootstraps, forecast gains from the
  Clark-West test for nested models.
"""

import json
import math
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
from scipy import stats
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.models import ResearchRun

HOLDOUT_START = date(2023, 7, 1)
PERIODS = ("design", "validation", "holdout")


class HoldoutError(RuntimeError):
    pass


# --- the trial log and the holdout guard ---


def _canonical(variant: dict[str, Any]) -> str:
    return json.dumps(variant, sort_keys=True, default=str)


def _code_version() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5
        )
        return out.stdout.strip() or None
    except OSError, subprocess.SubprocessError:
        return None


def log(
    session: Session,
    hypothesis: str,
    variant: dict[str, Any],
    period: str,
    metrics: dict[str, Any],
    frozen: bool = False,
) -> ResearchRun:
    if period not in PERIODS:
        raise ValueError(f"period is one of {PERIODS}")
    if period == "holdout" and not holdout_allowed(session, hypothesis, variant):
        raise HoldoutError(f"{hypothesis}: the holdout needs a frozen variant, used once")
    run = ResearchRun(
        hypothesis=hypothesis,
        variant=_canonical(variant),
        period=period,
        frozen=frozen,
        ran_at=datetime.now(UTC),
        code_version=_code_version(),
        metrics=json.dumps(metrics, default=str),
    )
    session.add(run)
    session.commit()
    return run


def freeze(session: Session, hypothesis: str, variant: dict[str, Any], why: str) -> ResearchRun:
    """Choose the design that the holdout will test (one per hypothesis)."""
    if session.scalar(
        select(func.count()).where(ResearchRun.hypothesis == hypothesis, ResearchRun.frozen)
    ):
        raise HoldoutError(f"{hypothesis} already has a frozen variant")
    return log(session, hypothesis, variant, "validation", {"frozen_because": why}, frozen=True)


def holdout_allowed(session: Session, hypothesis: str, variant: dict[str, Any]) -> bool:
    key = _canonical(variant)
    frozen = session.scalar(
        select(func.count()).where(
            ResearchRun.hypothesis == hypothesis, ResearchRun.frozen, ResearchRun.variant == key
        )
    )
    used = session.scalar(
        select(func.count()).where(
            ResearchRun.hypothesis == hypothesis, ResearchRun.period == "holdout"
        )
    )
    return bool(frozen) and not used


def trials(session: Session, hypothesis: str) -> int:
    """Distinct variants evaluated so far (outside the holdout)."""
    return session.scalar(
        select(func.count(ResearchRun.variant.distinct())).where(
            ResearchRun.hypothesis == hypothesis, ResearchRun.period != "holdout"
        )
    )


def check_period(period: str, last: date) -> None:
    """Design and validation data must end before the holdout starts."""
    if period != "holdout" and last >= HOLDOUT_START:
        raise HoldoutError(f"{period} data reaches {last}, past the holdout start {HOLDOUT_START}")


def bonferroni_t(n_trials: int, alpha: float = 0.05) -> float:
    """The two-sided t needed at `alpha` after `n_trials` tries (Bonferroni: conservative,
    the floor the protocol asks for alongside t >= 3)."""
    return float(stats.norm.ppf(1 - alpha / (2 * max(n_trials, 1))))


# --- statistics ---


@dataclass(frozen=True)
class Estimate:
    value: float
    se: float | None
    low: float | None  # 95% interval
    high: float | None
    n: int

    @property
    def t(self) -> float | None:
        return self.value / self.se if self.se else None


def newey_west_mean(values: Sequence[float], lags: int | None = None) -> Estimate:
    """Mean with a Newey-West (HAC) standard error: right for overlapping returns, where a
    plain t-stat counts the same move several times. Default lags: the usual
    floor(4 (n/100)^(2/9))."""
    x = np.asarray([v for v in values if v is not None and not math.isnan(v)], dtype=float)
    n = len(x)
    if n < 3:
        return Estimate(float(x.mean()) if n else math.nan, None, None, None, n)
    lags = lags if lags is not None else int(4 * (n / 100) ** (2 / 9))
    e = x - x.mean()
    var = e @ e / n
    for k in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - k / (lags + 1)) * (e[k:] @ e[:-k]) / n
    se = math.sqrt(max(var, 0) / n)
    m = float(x.mean())
    return Estimate(m, se, m - 1.96 * se, m + 1.96 * se, n)


def block_bootstrap(
    values: Sequence[Any],
    statistic: Callable[[list[Any]], float],
    block: int,
    reps: int = 2000,
    seed: int = 0,
) -> Estimate:
    """Moving-block bootstrap of a statistic over a time-ordered sequence: resampling
    blocks keeps the autocorrelation that ordinary resampling destroys."""
    data = list(values)
    n = len(data)
    point = statistic(data)
    if n < 2 * block:
        return Estimate(point, None, None, None, n)
    rng = np.random.default_rng(seed)
    starts = n - block + 1
    draws = []
    for _ in range(reps):
        sample: list[Any] = []
        while len(sample) < n:
            s = int(rng.integers(starts))
            sample.extend(data[s : s + block])
        draws.append(statistic(sample[:n]))
    draws_a = np.asarray([d for d in draws if d is not None and not math.isnan(d)])
    return Estimate(
        point,
        float(draws_a.std(ddof=1)),
        float(np.percentile(draws_a, 2.5)),
        float(np.percentile(draws_a, 97.5)),
        n,
    )


def cluster_bootstrap(
    clusters: dict[Any, list[Any]],
    statistic: Callable[[list[Any]], float],
    reps: int = 2000,
    seed: int = 0,
) -> Estimate:
    """Bootstrap resampling whole clusters (companies, countries), for statistics over
    observations that aren't independent within a cluster."""
    groups = list(clusters.values())
    flat = [x for g in groups for x in g]
    point = statistic(flat)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(reps):
        picked = rng.integers(len(groups), size=len(groups))
        draws.append(statistic([x for i in picked for x in groups[i]]))
    draws_a = np.asarray([d for d in draws if d is not None and not math.isnan(d)])
    if not len(draws_a):
        return Estimate(point, None, None, None, len(flat))
    return Estimate(
        point,
        float(draws_a.std(ddof=1)),
        float(np.percentile(draws_a, 2.5)),
        float(np.percentile(draws_a, 97.5)),
        len(flat),
    )


def lift(flagged: Sequence[bool], outcome: Sequence[bool]) -> float:
    """Outcome rate among flagged over the overall rate (nan when undefined)."""
    n, hits = len(outcome), sum(outcome)
    k = sum(flagged)
    if not n or not hits or not k:
        return math.nan
    return (sum(1 for f, o in zip(flagged, outcome, strict=True) if f and o) / k) / (hits / n)


def cross_correlation(x: Sequence[float], y: Sequence[float], lags: range) -> dict[int, float]:
    """Correlation of x at t with y at t + lag for each lag: a positive peak lag means x
    leads y. Pairs with a missing value are skipped."""
    out = {}
    n = len(x)
    for lag in lags:
        pairs = [
            (x[t], y[t + lag])
            for t in range(max(0, -lag), min(n, n - lag))
            if x[t] is not None and y[t + lag] is not None
        ]
        if len(pairs) > 10:
            a, b = np.asarray(pairs, dtype=float).T
            out[lag] = float(np.corrcoef(a, b)[0, 1])
    return out


def clark_west(
    actual: Sequence[float], base: Sequence[float], model: Sequence[float], lags: int = 0
) -> tuple[float, float]:
    """Clark-West test that a larger model forecasts better than the nested base model
    (out of sample). Returns (t-statistic, one-sided p-value); HAC errors for overlapping
    forecast horizons via `lags`."""
    y, f1, f2 = (np.asarray(v, dtype=float) for v in (actual, base, model))
    adjusted = (y - f1) ** 2 - ((y - f2) ** 2 - (f1 - f2) ** 2)
    est = newey_west_mean(adjusted.tolist(), lags)
    t = est.t or 0.0
    return t, float(1 - stats.norm.cdf(t))


def out_of_sample_r2(
    actual: Sequence[float], base: Sequence[float], model: Sequence[float]
) -> float:
    """1 - MSE(model) / MSE(base): positive when the model beats the base out of sample."""
    y, f1, f2 = (np.asarray(v, dtype=float) for v in (actual, base, model))
    return float(1 - ((y - f2) ** 2).sum() / ((y - f1) ** 2).sum())


def auc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Area under the ROC curve: the chance a random positive scores above a random
    negative (ties count half)."""
    pos = [s for s, lab in zip(scores, labels, strict=True) if lab]
    neg = [s for s, lab in zip(scores, labels, strict=True) if not lab]
    if not pos or not neg:
        return math.nan
    ranks = stats.rankdata(np.asarray(pos + neg, dtype=float))
    return float((ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))
