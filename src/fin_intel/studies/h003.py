"""H003: when private credit reversals cause crises, Keen's conditions
(docs/research/H003-private-credit-conditions.md).

Two panels, built into one row per country and date with the predictors as public then
and what followed:

- BIS (quarterly, ~36 economies): private credit to GDP and its percentile within the
  country's own history to date, the private and government credit impulses, the Basel
  credit gap, the private debt service ratio against its trailing mean. Known 170 days
  after the quarter (credit.PUBLICATION_LAG); outcomes are measured from that date:
  a recession starting (OECD indicator), a real equity drawdown of 30% or more (OECD share
  prices over consumer prices), a systemic banking crisis starting (Macrohistory, 18
  economies).
- Macrohistory (yearly since 1870, 18 economies): the same quantities from total loans,
  public debt and GDP, known the next year; outcomes a crisis onset or a real equity
  drawdown within one or three years.

BIS data aren't vintaged (revisions are visible); the Macrohistory data are compiled
after the fact. Both are research panels, not trading data.
"""

import math
from collections import defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import credit, macro, macrohistory, research
from fin_intel.models import EconomicObservation

HYPOTHESIS = "H003"
TIME_HOLDOUT = date(2015, 1, 1)  # quarters from here are the time holdout
HIGH_DEBT = 2 / 3  # percentile of a country's own history to date
MIN_HISTORY = 20  # observations before a percentile or trailing mean counts
DRAWDOWN = -0.30
WINDOWS = {12: 12, 36: 36}  # months
FEATURES_CORE = ("debt_pct", "impulse", "debt_x_impulse", "gov_impulse")
FEATURES_FULL = (*FEATURES_CORE, "credit_gap", "dsr_dev")


@dataclass
class Row:
    panel: str  # bis or jst
    country: str  # ISO alpha-3
    period: date  # quarter or year end the data describe
    known_on: date
    debt: float | None = None
    debt_pct: float | None = None
    impulse: float | None = None
    gov_impulse: float | None = None
    credit_gap: float | None = None
    dsr_dev: float | None = None
    household_share: float | None = None
    outcomes: dict[str, bool | None] = field(default_factory=dict)

    @property
    def debt_x_impulse(self) -> float | None:
        if self.debt_pct is None or self.impulse is None:
            return None
        return self.debt_pct * self.impulse

    @property
    def signal(self) -> bool | None:
        """Keen's condition: high debt and a reversing credit flow."""
        if self.debt_pct is None or self.impulse is None:
            return None
        return self.debt_pct >= HIGH_DEBT and self.impulse < 0

    @property
    def offset(self) -> bool | None:
        """The government's impulse makes up for the private one."""
        if self.impulse is None or self.gov_impulse is None:
            return None
        return self.impulse + self.gov_impulse >= 0

    def feature(self, name: str) -> float | None:
        return getattr(self, name)


# --- building rows ---


def percentiles(values: list[float | None]) -> list[float | None]:
    """Each value's percentile among the values up to and including it (expanding)."""
    out: list[float | None] = []
    seen: list[float] = []
    for v in values:
        if v is None:
            out.append(None)
            continue
        seen.append(v)
        out.append(sum(1 for s in seen if s <= v) / len(seen) if len(seen) >= MIN_HISTORY else None)
    return out


def trailing_deviation(values: list[float | None], window: int) -> list[float | None]:
    out: list[float | None] = []
    for i, v in enumerate(values):
        past = [x for x in values[max(0, i - window) : i] if x is not None]
        out.append(
            v - sum(past) / len(past) if v is not None and len(past) >= MIN_HISTORY else None
        )
    return out


def add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return date(year, month, min(day.day, 28))


def recession_starts(monthly: list[tuple[date, float]]) -> tuple[set[date], date | None]:
    """Months a recession indicator turns from 0 to 1, and the last month covered."""
    starts = {d for (p, a), (d, b) in zip(monthly, monthly[1:], strict=False) if a < 0.5 <= b}
    return starts, monthly[-1][0] if monthly else None


def in_recession(monthly: dict[date, float], day: date) -> bool | None:
    month = date(day.year, day.month, 1)
    value = monthly.get(month)
    return None if value is None else value >= 0.5


def window_outcome(events: set[date], last: date | None, start: date, months: int) -> bool | None:
    """An event within (start, start + months], or None when data don't cover the window."""
    end = add_months(start, months)
    if last is None or last < end:
        return None
    return any(start < e <= end for e in events)


def drawdown_outcome(
    index: list[tuple[date, float]],
    start: date,
    months: int,
    threshold: float = DRAWDOWN,
    min_points: int = 1,
) -> bool | None:
    """The real index falls `threshold` or more below its level at `start` within the
    window (None when not covered, or with fewer than `min_points` values inside)."""
    end = add_months(start, months)
    if not index or index[-1][0] < end:
        return None
    before = [v for d, v in index if d <= start]
    if not before:
        return None
    base = before[-1]
    inside = [v for d, v in index if start < d <= end]
    if len(inside) < min_points:
        return None
    return min(inside) / base - 1 <= threshold


def _observations(session: Session, series_id: str) -> list[tuple[date, float]]:
    return [
        (d, v)
        for d, v in session.execute(
            select(EconomicObservation.date, EconomicObservation.value)
            .where(
                EconomicObservation.series_id == series_id, EconomicObservation.value.is_not(None)
            )
            .order_by(EconomicObservation.date)
        )
    ]


def real_index(
    prices: list[tuple[date, float]], cpi: list[tuple[date, float]]
) -> list[tuple[date, float]]:
    out, j, level = [], 0, None
    for d, p in prices:
        while j < len(cpi) and cpi[j][0] <= d:
            level = cpi[j][1]
            j += 1
        if level:
            out.append((d, p / level))
    return out


def jst_crisis_onsets(session: Session) -> dict[str, tuple[set[date], date]]:
    """ISO3 -> (crisis onset dates as mid-year, last year covered as its end)."""
    data = macrohistory.panel(session, ["crisisJST"])
    by_country: dict[str, dict[int, float]] = defaultdict(dict)
    for (country, year), values in data.items():
        if "crisisJST" in values:
            by_country[country][year] = values["crisisJST"]
    return {
        c: ({date(y, 7, 1) for y, v in years.items() if v >= 0.5}, date(max(years), 12, 31))
        for c, years in by_country.items()
    }


def bis_rows(session: Session) -> list[Row]:
    crises = jst_crisis_onsets(session)
    rows = []
    for iso2, iso3 in macro.PANEL_COUNTRIES.items():
        debt = dict(credit.history(session, iso2, "private_credit_gdp"))
        if not debt:
            continue
        quarters = sorted(debt)
        impulse = dict(credit.history(session, iso2, "credit_impulse"))
        gov = dict(credit.history(session, iso2, "government_impulse"))
        gap = dict(credit.history(session, iso2, "credit_gap"))
        dsr = dict(credit.history(session, iso2, "dsr_private"))
        households = dict(credit.history(session, iso2, "household_credit_gdp"))
        pct = dict(zip(quarters, percentiles([debt[q] for q in quarters]), strict=True))
        dsr_q = sorted(dsr)
        dsr_dev = dict(zip(dsr_q, trailing_deviation([dsr[q] for q in dsr_q], 40), strict=True))
        recession = (
            []
            if iso2 in macro.NO_RECESSION_SERIES
            else _observations(session, macro.recession_series(iso2))
        )
        starts, last_rec = recession_starts(recession)
        rec_by_month = dict(recession)
        equity = real_index(
            _observations(session, macro.share_price_series(iso2)),
            _observations(session, macro.cpi_series(iso2)),
        )
        crisis = crises.get(iso3)
        for q in quarters:
            known = q + credit.PUBLICATION_LAG
            row = Row(
                panel="bis",
                country=iso3,
                period=q,
                known_on=known,
                debt=debt[q],
                debt_pct=pct.get(q),
                impulse=impulse.get(q),
                gov_impulse=gov.get(q),
                credit_gap=gap.get(q),
                dsr_dev=dsr_dev.get(q),
                household_share=households[q] / debt[q] if households.get(q) and debt[q] else None,
            )
            already = in_recession(rec_by_month, known)
            for label, months in WINDOWS.items():
                row.outcomes[f"recession_{label}"] = (
                    None
                    if already in (None, True)
                    else window_outcome(starts, last_rec, known, months)
                )
                row.outcomes[f"drawdown_{label}"] = drawdown_outcome(equity, known, months)
                row.outcomes[f"crisis_{label}"] = (
                    window_outcome(crisis[0], crisis[1], known, months) if crisis else None
                )
            rows.append(row)
    return rows


def yearly_impulse(
    levels: dict[int, float | None], gdp: dict[int, float | None], y: int
) -> float | None:
    """The change in a year's new borrowing, as % of that year's GDP."""
    a, b, c = levels.get(y), levels.get(y - 1), levels.get(y - 2)
    g = gdp.get(y)
    if a is None or b is None or c is None or not g:
        return None
    return ((a - b) - (b - c)) / g * 100


def jst_rows(session: Session) -> list[Row]:
    data = macrohistory.panel(
        session, ["tloans", "gdp", "debtgdp", "crisisJST", "eq_capgain", "cpi"]
    )
    onsets = jst_crisis_onsets(session)
    by_country: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
    for (country, year), values in data.items():
        by_country[country][year] = values
    rows = []
    for country, years in by_country.items():
        ordered = sorted(years)
        loans = {y: years[y].get("tloans") for y in ordered}
        gdp = {y: years[y].get("gdp") for y in ordered}
        public = {
            y: years[y]["debtgdp"] * gdp[y]
            if years[y].get("debtgdp") is not None and gdp[y]
            else None
            for y in ordered
        }
        ratio = {y: loans[y] / gdp[y] * 100 if loans[y] and gdp[y] else None for y in ordered}
        pct = dict(zip(ordered, percentiles([ratio[y] for y in ordered]), strict=True))
        # A real equity index from yearly capital gains deflated by consumer prices; years
        # without data are skipped, and windows missing a year get no outcome.
        index: list[tuple[date, float]] = []
        level = 1.0
        for y in ordered:
            v, prev = years[y], years.get(y - 1, {})
            gain, cpi, cpi0 = v.get("eq_capgain"), v.get("cpi"), prev.get("cpi")
            if gain is None or not cpi or not cpi0:
                continue
            level *= (1 + gain) / (cpi / cpi0)
            index.append((date(y, 12, 31), level))
        crisis = onsets.get(country, (set(), date(1870, 1, 1)))

        for y in ordered:
            known = date(y + 1, 6, 30)
            row = Row(
                panel="jst",
                country=country,
                period=date(y, 12, 31),
                known_on=known,
                debt=ratio[y],
                debt_pct=pct.get(y),
                impulse=yearly_impulse(loans, gdp, y),
                gov_impulse=yearly_impulse(public, gdp, y),
            )
            for label, months in WINDOWS.items():
                row.outcomes[f"crisis_{label}"] = window_outcome(
                    crisis[0], crisis[1], known, months
                )
                row.outcomes[f"drawdown_{label}"] = drawdown_outcome(
                    index, known, months, min_points=months // 12
                )
            rows.append(row)
    return rows


# --- tests ---


@dataclass
class Rates:
    outcome: str
    group: str
    n: int
    rate: float | None
    base: float | None
    ratio: float | None
    ratio_low: float | None
    ratio_high: float | None


def conditional_rates(rows: list[Row], outcome: str, reps: int = 1000) -> list[Rates]:
    """Outcome rates in Keen's condition, with and without the public offset, against the
    base rate; ratio intervals from a bootstrap over countries."""
    usable = [r for r in rows if r.outcomes.get(outcome) is not None and r.signal is not None]
    groups: dict[str, Callable[[Row], bool]] = {
        "high debt, negative impulse": lambda r: bool(r.signal),
        "  with public offset": lambda r: bool(r.signal) and r.offset is True,
        "  without public offset": lambda r: bool(r.signal) and r.offset is False,
        "high debt, positive impulse": lambda r: r.debt_pct >= HIGH_DEBT and r.impulse >= 0,
        "low debt, negative impulse": lambda r: r.debt_pct < HIGH_DEBT and r.impulse < 0,
    }

    def rate(rs: list[Row], pick: Callable[[Row], bool]) -> float | None:
        chosen = [r for r in rs if pick(r)]
        return sum(r.outcomes[outcome] for r in chosen) / len(chosen) if chosen else None

    def ratio(rs: list[Row], pick: Callable[[Row], bool]) -> float:
        base, inside = rate(rs, lambda r: True), rate(rs, pick)
        return inside / base if inside is not None and base else math.nan

    by_country: dict[str, list[Row]] = defaultdict(list)
    for r in usable:
        by_country[r.country].append(r)
    base = rate(usable, lambda r: True)
    out = []
    for name, pick in groups.items():
        chosen = [r for r in usable if pick(r)]
        est = research.cluster_bootstrap(by_country, lambda rs, p=pick: ratio(rs, p), reps=reps)
        out.append(
            Rates(
                outcome,
                name,
                len(chosen),
                rate(usable, pick),
                base,
                est.value if not math.isnan(est.value) else None,
                est.low,
                est.high,
            )
        )
    return out


def _matrix(rows: list[Row], features: tuple[str, ...]) -> np.ndarray:
    return np.asarray([[r.feature(f) for f in features] for r in rows], dtype=float)


def logit(rows: list[Row], outcome: str, features: tuple[str, ...]) -> dict[str, Any]:
    """Pooled logit with standardized features and errors clustered by country."""
    import statsmodels.api as sm

    usable = [
        r
        for r in rows
        if r.outcomes.get(outcome) is not None and all(r.feature(f) is not None for f in features)
    ]
    y = np.asarray([float(r.outcomes[outcome]) for r in usable])
    if len(usable) < 50 or y.sum() < 5:
        return {"n": len(usable), "events": int(y.sum()), "skipped": "too few events"}
    X = _matrix(usable, features)
    X = (X - X.mean(axis=0)) / X.std(axis=0)
    groups = np.asarray([hash(r.country) % 10**8 for r in usable])
    fit = sm.Logit(y, sm.add_constant(X)).fit(
        disp=0, cov_type="cluster", cov_kwds={"groups": groups}
    )
    return {
        "n": len(usable),
        "events": int(y.sum()),
        "countries": len({r.country for r in usable}),
        "coef": dict(zip(features, fit.params[1:].round(3).tolist(), strict=True)),
        "z": dict(zip(features, (fit.params[1:] / fit.bse[1:]).round(2).tolist(), strict=True)),
    }


def _fit_predict(train: list[Row], test: list[Row], outcome: str, features: tuple[str, ...]):
    import statsmodels.api as sm

    X = _matrix(train, features)
    mean, std = X.mean(axis=0), X.std(axis=0)
    std[std == 0] = 1
    y = np.asarray([float(r.outcomes[outcome]) for r in train])
    fit = sm.Logit(y, sm.add_constant((X - mean) / std)).fit(disp=0, maxiter=200)
    return fit.predict(sm.add_constant((_matrix(test, features) - mean) / std, has_constant="add"))


def loco(
    rows: list[Row], outcome: str, base_features: tuple[str, ...], model_features: tuple[str, ...]
) -> dict[str, Any]:
    """Out-of-country prediction (fit on all other countries, predict the held-out one):
    AUC and precision at a 10% alert rate, for a base model and the model adding Keen's
    quantities."""
    features = tuple(dict.fromkeys((*base_features, *model_features)))
    usable = [
        r
        for r in rows
        if r.outcomes.get(outcome) is not None and all(r.feature(f) is not None for f in features)
    ]
    countries = sorted({r.country for r in usable})
    labels, base_scores, model_scores = [], [], []
    for held in countries:
        train = [r for r in usable if r.country != held]
        test = [r for r in usable if r.country == held]
        if sum(r.outcomes[outcome] for r in train) < 5:
            continue
        labels += [bool(r.outcomes[outcome]) for r in test]
        base_scores += list(_fit_predict(train, test, outcome, base_features))
        model_scores += list(_fit_predict(train, test, outcome, model_features))
    if not labels or not any(labels):
        return {"n": len(labels), "skipped": "no events"}

    def precision_at(scores: list[float], share: float = 0.10) -> float:
        cut = np.quantile(scores, 1 - share)
        flagged = [lab for s, lab in zip(scores, labels, strict=True) if s >= cut]
        return sum(flagged) / len(flagged) if flagged else math.nan

    return {
        "n": len(labels),
        "events": sum(labels),
        "countries": len(countries),
        "auc_base": research.auc(base_scores, labels),
        "auc_model": research.auc(model_scores, labels),
        "precision10_base": precision_at(base_scores),
        "precision10_model": precision_at(model_scores),
        "base_rate": sum(labels) / len(labels),
    }


def episodes(rows: list[Row]) -> list[dict[str, Any]]:
    """The first quarter (or year) of each run of Keen's condition, with what followed."""
    out = []
    by_country: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_country[r.country].append(r)
    for country, rs in by_country.items():
        rs.sort(key=lambda r: r.period)
        previous = False
        for r in rs:
            on = bool(r.signal)
            if on and not previous:
                out.append(
                    {
                        "country": country,
                        "period": r.period,
                        "debt": round(r.debt, 1) if r.debt else None,
                        "impulse": round(r.impulse, 2) if r.impulse is not None else None,
                        "gov_impulse": round(r.gov_impulse, 2)
                        if r.gov_impulse is not None
                        else None,
                        **{k: v for k, v in r.outcomes.items() if k.endswith("_36")},
                    }
                )
            previous = on
    return out


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in ("design", "validation"):
        raise ValueError("H003 runs design or validation here; the holdout needs a frozen variant")
    rows = [*bis_rows(session), *jst_rows(session)]
    rows = [r for r in rows if r.period < TIME_HOLDOUT]
    results: list[dict[str, Any]] = []
    for panel in ("bis", "jst"):
        panel_rows = [r for r in rows if r.panel == panel]
        outcomes = sorted({k for r in panel_rows for k in r.outcomes})
        for outcome in outcomes:
            rates = [asdict(x) for x in conditional_rates(panel_rows, outcome)]
            features = FEATURES_CORE
            entry = {
                "panel": panel,
                "outcome": outcome,
                "rates": rates,
                "logit": logit(panel_rows, outcome, features),
            }
            if panel == "bis":
                entry["logit_full"] = logit(panel_rows, outcome, FEATURES_FULL)
                if period == "validation":
                    entry["loco"] = loco(
                        panel_rows, outcome, ("credit_gap",), ("credit_gap", *FEATURES_CORE)
                    )
            elif period == "validation":
                entry["loco"] = loco(panel_rows, outcome, ("debt_pct",), FEATURES_CORE)
            results.append(entry)
            if log:
                research.log(
                    session, HYPOTHESIS, {"panel": panel, "outcome": outcome}, period, entry
                )
        results.append({"panel": panel, "episodes": episodes(panel_rows)})
    return results
