"""H008: opportunistic insider purchases predict excess returns; routine ones don't
(docs/research/H008-opportunistic-insiders.md).

A company is flagged in the quarter an officer or director's open-market purchase (not
under a 10b5-1 plan, at least $10,000) was filed. The insider is *routine* if they bought
the company's shares in the same calendar month in each of the three prior years
(Cohen, Malloy and Pomorski 2012), *new* if they bought nothing in those years,
*opportunistic* otherwise (new insiders count as opportunistic in the main reading and are
dropped in the strict one). Prices are the 13F quarterly proxy (quarterly_prices, three or
more filers), split-adjusted; returns run from the signal quarter's end to one and four
quarters later. Calendar-time portfolios against size-decile peers decide; the routine
portfolio, every purchase, the cluster variant, a pooled regression with sector and
momentum controls, and exits are reported alongside.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import population, research, sectors
from fin_intel.models import (
    CorporateAction,
    CorporateEvent,
    InsiderTransaction,
    Issuer,
    QuarterlyPrice,
)

HYPOTHESIS = "H008"
MIN_PURCHASE = 10_000.0
MIN_FILERS = 3
ROUTINE_YEARS = 3
HORIZONS = (1, 4)
PERIODS = {  # signal quarters (quarter ends)
    "design": (date(2013, 9, 30), date(2018, 12, 31)),
    "validation": (date(2019, 3, 31), date(2022, 6, 30)),
    "holdout": (date(2022, 9, 30), date(2100, 1, 1)),
}
PRICES_FROM = date(2013, 6, 30)
HISTORY_FROM = date(2010, 1, 1)  # purchases needed to classify 2013's insiders
EXIT_FORMS = ("15-12B", "15-12G", "15-15D", "25-NSE")
DECILES = 10
# Nothing is chosen between variants: the signal, floor and horizon are the registered ones.
FROZEN = {"signal": "opportunistic", "horizon": 1, "min_purchase": MIN_PURCHASE,
          "routine_years": ROUTINE_YEARS}  # fmt: skip


@dataclass(frozen=True)
class Purchase:
    cik: int
    insider: str  # the owner's CIK, or their name when the filing carries none
    trans_date: date
    filing_date: date


@dataclass(frozen=True)
class Price:
    price: float
    shares: float
    value_total: float
    security_id: int | None
    cusip: str = ""


MAX_RETURN = 3.0  # a quarter's move beyond +300% is read as a proxy error, not a return


Flag = dict[str, set[str]]  # kind -> insiders (opportunistic, routine, new, all)


def quarter_end(day: date) -> date:
    month = 3 * ((day.month - 1) // 3 + 1)
    first_next = date(day.year + (month == 12), 1 if month == 12 else month + 1, 1)
    return first_next - timedelta(days=1)


def classify(purchases: list[Purchase]) -> dict[tuple[int, date], Flag]:
    """Insiders buying per company and signal quarter (the filing's quarter), by kind."""
    months: dict[tuple[int, str], set[tuple[int, int]]] = defaultdict(set)
    dates: dict[tuple[int, str], list[date]] = defaultdict(list)
    for p in purchases:
        months[p.cik, p.insider].add((p.trans_date.year, p.trans_date.month))
        dates[p.cik, p.insider].append(p.trans_date)
    flags: dict[tuple[int, date], Flag] = defaultdict(lambda: defaultdict(set))
    for p in purchases:
        key = (p.cik, p.insider)
        same_month = all(
            (p.trans_date.year - k, p.trans_date.month) in months[key]
            for k in range(1, ROUTINE_YEARS + 1)
        )
        earlier = [
            d for d in dates[key]
            if p.trans_date - timedelta(days=365 * ROUTINE_YEARS) <= d < p.trans_date
        ]  # fmt: skip
        kind = "routine" if same_month else "new" if not earlier else "opportunistic"
        flag = flags[p.cik, quarter_end(p.filing_date)]
        flag[kind].add(p.insider)
        flag["all"].add(p.insider)
    return flags


def split_factor(
    splits: list[tuple[date, float]] | None, start: date, end: date, p0: Price, p1: Price
) -> float:
    """What to multiply the later price by: recorded splits in (start, end], or, for a
    company with no security row, a split read off the holdings themselves (shares held
    jumping by an integer ratio while the price moved by its inverse)."""
    if splits is not None:
        return math.prod(v for d, v in splits if start < d <= end) or 1.0
    if not p0.shares or not p1.shares:
        return 1.0
    s, r = p1.shares / p0.shares, p1.price / p0.price
    if abs(math.log(r * s)) > 0.15:
        return 1.0
    ratio = s if s >= 1 else 1 / s
    nearest = round(ratio)
    if nearest >= 2 and abs(ratio - nearest) <= 0.1 * nearest:
        return float(nearest) if s >= 1 else 1 / nearest
    return 1.0


def forward_return(
    prices: dict[date, Price], splits: dict[int, list[tuple[date, float]]], q: date, later: date
) -> float | None:
    """The return of the same CUSIP between the two quarter ends; None across a change of
    CUSIP (a retired or reclassified security prices on a different scale) or beyond
    MAX_RETURN (a proxy error), both counted by the caller as missing."""
    p0, p1 = prices.get(q), prices.get(later)
    if p0 is None or p1 is None or p0.price <= 0 or p0.cusip != p1.cusip:
        return None
    recorded = splits.get(p0.security_id) if p0.security_id is not None else None
    r = p1.price * split_factor(recorded, q, later, p0, p1) / p0.price - 1
    return r if r <= MAX_RETURN else None


def deciles(values: dict[int, float]) -> dict[int, int]:
    """Each company's decile (0-9) of a quarter's cross-section."""
    ranked = sorted(values, key=lambda c: values[c])
    return {c: min(i * DECILES // len(ranked), DECILES - 1) for i, c in enumerate(ranked)}


@dataclass
class Quarter:
    end: date
    later: date
    returns: dict[int, float]  # cik -> return over the horizon (universe: every priced company)
    size: dict[int, int]  # decile
    momentum: dict[int, int | None]
    sector: dict[int, str | None]
    peers: dict[int, float]  # size decile -> equal-weight return

    def excess(self, cik: int) -> float:
        return self.returns[cik] - self.peers[self.size[cik]]


def build_quarters(
    prices: dict[int, dict[date, Price]],
    splits: dict[int, list[tuple[date, float]]],
    sic: dict[int, int | None],
    members: set[int],
    horizon: int,
) -> list[Quarter]:
    ends = sorted({q for by_q in prices.values() for q in by_q})
    out = []
    for i, q in enumerate(ends):
        if i + horizon >= len(ends):
            break
        later = ends[i + horizon]
        returns, values, momentum = {}, {}, {}
        for cik, by_q in prices.items():
            if cik not in members or q not in by_q:
                continue
            r = forward_return(by_q, splits, q, later)
            if r is None:
                continue
            returns[cik] = r
            values[cik] = by_q[q].value_total
            momentum[cik] = forward_return(by_q, splits, ends[i - 2], q) if i >= 2 else None
        if len(returns) < 20:
            continue
        size = deciles(values)
        mom_known = {c: v for c, v in momentum.items() if v is not None}
        mom_deciles = deciles(mom_known) if mom_known else {}
        peers = defaultdict(list)
        for cik, r in returns.items():
            peers[size[cik]].append(r)
        out.append(
            Quarter(
                end=q,
                later=later,
                returns=returns,
                size=size,
                momentum={c: mom_deciles.get(c) for c in returns},
                sector={c: sectors.sector(sic.get(c)) for c in returns},
                peers={d: float(np.mean(rs)) for d, rs in peers.items()},
            )
        )
    return out


# --- tests -----------------------------------------------------------------------------


def portfolio(
    quarters: list[Quarter], flags: dict[tuple[int, date], Flag], select_kind, lags: int
) -> dict[str, Any]:
    """Calendar-time equal-weight portfolio of the companies `select_kind(flag)` picks in
    each quarter, held over the horizon: the quarterly series of excess returns over
    size-decile peers, its Newey-West mean, median and hit rate."""
    series, sizes = [], []
    for quarter in quarters:
        picked = [
            cik for (cik, q), flag in flags.items()
            if q == quarter.end and cik in quarter.returns and select_kind(flag)
        ]  # fmt: skip
        if not picked:
            continue
        series.append(float(np.mean([quarter.excess(c) for c in picked])))
        sizes.append(len(picked))
    if len(series) < 8:
        return {"quarters": len(series)}
    est = research.newey_west_mean(series, lags=lags)
    return {
        "quarters": len(series),
        "companies_per_quarter": float(np.mean(sizes)),
        "mean_excess": est.value,
        "t": est.t,
        "p": research.one_sided_p(est),
        "median_excess": median(series),
        "hit_rate": sum(1 for s in series if s > 0) / len(series),
        "series": series,
    }


def difference(a: dict[str, Any], b: dict[str, Any], lags: int) -> dict[str, Any]:
    if "series" not in a or "series" not in b or len(a["series"]) != len(b["series"]):
        return {"quarters": 0}
    diff = [x - y for x, y in zip(a["series"], b["series"], strict=True)]
    est = research.newey_west_mean(diff, lags=lags)
    return {"quarters": len(diff), "mean": est.value, "t": est.t, "p": research.one_sided_p(est)}


def after_controls(
    quarters: list[Quarter], flags: dict[tuple[int, date], Flag], reps: int = 1000
) -> dict[str, Any]:
    """Pooled regression of flagged companies' excess returns on sector and momentum-decile
    indicators (demeaned, so the constant is the adjusted mean), errors from a bootstrap
    that resamples whole quarters."""
    rows: dict[date, list[tuple[float, str, int | None]]] = defaultdict(list)
    for quarter in quarters:
        for (cik, q), flag in flags.items():
            if q == quarter.end and cik in quarter.returns and flag.get("opportunistic"):
                rows[q].append(
                    (quarter.excess(cik), quarter.sector[cik] or "none", quarter.momentum[cik])
                )
    flat = [r for group in rows.values() for r in group]
    if len(flat) < 50:
        return {"rows": len(flat)}
    sectors_ = sorted({s for _, s, _ in flat})
    moms = sorted({m for _, _, m in flat if m is not None})

    def design(sample: list[tuple[float, str, int | None]]) -> tuple[np.ndarray, np.ndarray]:
        y = np.asarray([r for r, _, _ in sample])
        X = np.ones((len(sample), 1 + len(sectors_) + len(moms)))
        X[:, 1:] = 0.0
        for i, (_, s, m) in enumerate(sample):
            X[i, 1 + sectors_.index(s)] = 1.0
            if m is not None:
                X[i, 1 + len(sectors_) + moms.index(m)] = 1.0
        X[:, 1:] -= X[:, 1:].mean(axis=0)
        return y, X

    def constant(sample: list[tuple[float, str, int | None]]) -> float:
        y, X = design(sample)
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        return float(beta[0])

    est = research.cluster_bootstrap(rows, constant, reps=reps)
    return {"rows": len(flat), "adjusted_mean": est.value, "low": est.low, "high": est.high}


def exits(
    quarters: list[Quarter],
    prices: dict[int, dict[date, Price]],
    flags: dict[tuple[int, date], Flag],
    events: dict[int, list[date]],
) -> dict[str, Any]:
    """Flagged companies with a price at the signal quarter but none at the horizon: how
    many, and how many had a bankruptcy or deregistration filing in the window (read as
    -100% in the second reading)."""
    gone = failed = screened = 0
    for quarter in quarters:
        for (cik, q), flag in flags.items():
            if q != quarter.end or not flag.get("opportunistic"):
                continue
            by_q = prices.get(cik, {})
            if q in by_q and quarter.later not in by_q:
                gone += 1
                if any(q < d <= quarter.later for d in events.get(cik, [])):
                    failed += 1
            elif q in by_q and cik not in quarter.returns:
                screened += 1  # priced at both ends, but across CUSIPs or beyond MAX_RETURN
    return {
        "without_price_at_horizon": gone,
        "with_exit_filing": failed,
        "screened_as_proxy_error": screened,
    }


def analyze(
    purchases: list[Purchase],
    prices: dict[int, dict[date, Price]],
    splits: dict[int, list[tuple[date, float]]],
    sic: dict[int, int | None],
    members: set[int],
    events: dict[int, list[date]],
    window: tuple[date, date],
    reps: int = 1000,
) -> dict[str, Any]:
    flags = {k: f for k, f in classify(purchases).items() if window[0] <= k[1] <= window[1]}
    flagged = len(flags)
    priced = sum(1 for cik, q in flags if q in prices.get(cik, {}))
    result: dict[str, Any] = {
        "flagged_company_quarters": flagged,
        "with_price": priced,
        "coverage": priced / flagged if flagged else None,
    }
    for horizon in HORIZONS:
        quarters = [q for q in build_quarters(prices, splits, sic, members, horizon)
                    if window[0] <= q.end <= window[1]]  # fmt: skip
        lags = horizon - 1
        opp = portfolio(quarters, flags, lambda f: bool(f.get("opportunistic")), lags)
        strict = portfolio(
            quarters, flags, lambda f: bool(f.get("opportunistic")) and not f.get("new"), lags
        )
        routine = portfolio(quarters, flags, lambda f: bool(f.get("routine")), lags)
        every = portfolio(quarters, flags, lambda f: bool(f.get("all")), lags)
        cluster = portfolio(quarters, flags, lambda f: len(f.get("opportunistic", ())) >= 2, lags)
        block: dict[str, Any] = {
            "quarters_available": len(quarters),
            "opportunistic": opp,
            "opportunistic_strict": strict,
            "routine": routine,
            "all_purchases": every,
            "cluster": cluster,
            "opportunistic_minus_routine": difference(opp, routine, lags),
            "opportunistic_minus_all": difference(opp, every, lags),
            "exits": exits(quarters, prices, flags, events),
        }
        if horizon == 1:
            block["after_controls"] = after_controls(quarters, flags, reps)
        for d in (opp, strict, routine, every, cluster):
            d.pop("series", None)
        result[f"h{horizon}"] = block
    result["verdict"] = verdict(result)
    return result


def verdict(result: dict[str, Any]) -> str:
    h1 = result.get("h1", {})
    opp, routine, controls = (
        h1.get("opportunistic", {}),
        h1.get("routine", {}),
        h1.get("after_controls", {}),
    )
    if "mean_excess" not in opp:
        return "too few quarters"
    strong = opp["mean_excess"] >= 0.01 and (opp["t"] or 0) >= 2
    beats_routine = "mean_excess" not in routine or opp["mean_excess"] > routine["mean_excess"]
    holds = controls.get("adjusted_mean", 0) > 0
    if strong and beats_routine and holds:
        return "support"
    if opp["mean_excess"] <= 0 or not beats_routine:
        return "rejection"
    return "weak"


# --- data ------------------------------------------------------------------------------


def load_purchases(session: Session, end: date) -> list[Purchase]:
    rows = session.execute(
        select(
            InsiderTransaction.issuer_cik,
            InsiderTransaction.owner_cik,
            InsiderTransaction.owner_name,
            InsiderTransaction.relationship,
            InsiderTransaction.trans_date,
            InsiderTransaction.filing_date,
            InsiderTransaction.shares,
            InsiderTransaction.price,
        ).where(
            InsiderTransaction.trans_code == "P",
            InsiderTransaction.acquired_disposed == "A",
            InsiderTransaction.plan_10b5_1.is_(False),
            InsiderTransaction.trans_date >= HISTORY_FROM,
            InsiderTransaction.filing_date <= end,
        )
    ).all()
    out = []
    for cik, owner_cik, owner_name, relationship, trans_date, filed, shares, price in rows:
        rel = (relationship or "").lower()
        if "director" not in rel and "officer" not in rel:
            continue
        if not shares or not price or shares * price < MIN_PURCHASE or filed is None:
            continue
        insider = str(owner_cik) if owner_cik else f"name:{(owner_name or '').strip().lower()}"
        out.append(Purchase(cik, insider, trans_date, filed))
    return out


def load_prices(session: Session, end: date) -> dict[int, dict[date, Price]]:
    """Per company and quarter, the CUSIP institutions held the most of."""
    rows = session.execute(
        select(
            QuarterlyPrice.cik,
            QuarterlyPrice.period,
            QuarterlyPrice.price,
            QuarterlyPrice.shares_total,
            QuarterlyPrice.value_total,
            QuarterlyPrice.security_id,
            QuarterlyPrice.cusip,
        ).where(
            QuarterlyPrice.cik.is_not(None),
            QuarterlyPrice.filers >= MIN_FILERS,
            QuarterlyPrice.period >= PRICES_FROM,
            QuarterlyPrice.period <= end,
        )
    ).all()
    out: dict[int, dict[date, Price]] = defaultdict(dict)
    for cik, period, price, shares, value, security_id, cusip in rows:
        current = out[cik].get(period)
        if current is None or value > current.value_total:
            out[cik][period] = Price(price, shares, value, security_id, cusip)
    return out


def load_splits(session: Session) -> dict[int, list[tuple[date, float]]]:
    out: dict[int, list[tuple[date, float]]] = defaultdict(list)
    for security_id, ex_date, value in session.execute(
        select(CorporateAction.security_id, CorporateAction.ex_date, CorporateAction.value).where(
            CorporateAction.action == "split", CorporateAction.value > 0
        )
    ):
        out[security_id].append((ex_date, value))
    return out


def load_exit_events(session: Session) -> dict[int, list[date]]:
    out: dict[int, list[date]] = defaultdict(list)
    for cik, filed in session.execute(
        select(CorporateEvent.cik, CorporateEvent.filed).where(
            CorporateEvent.filed >= PRICES_FROM,
            (CorporateEvent.form.in_(EXIT_FORMS))
            | (CorporateEvent.form.startswith("8-K") & (CorporateEvent.item == "1.03")),
        )
    ):
        out[cik].append(filed)
    return out


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in PERIODS:
        raise ValueError(f"period is one of {tuple(PERIODS)}")
    if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, FROZEN):
        raise research.HoldoutError("freeze the variant first (study H008 --freeze), once")
    window = PERIODS[period]
    end = research.HOLDOUT_START - timedelta(days=1) if period != "holdout" else date.today()
    prices = load_prices(session, end)
    if period != "holdout":
        research.check_period(period, max(q for by_q in prices.values() for q in by_q))
    sic = dict(session.execute(select(Issuer.cik, Issuer.sic)).all())
    result = analyze(
        load_purchases(session, end),
        prices,
        load_splits(session),
        sic,
        population.members(session),
        load_exit_events(session),
        window,
    )
    result["period"] = period
    if log:
        research.log(session, HYPOTHESIS, FROZEN, period, result)
    return [result]
