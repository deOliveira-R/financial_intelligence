"""H010: the new best ideas of concentrated, patient managers beat their peers
(docs/research/H010-best-ideas.md).

A manager is eligible at a quarter end when their book (manager_quarters) has 10 to 40
equity positions worth at least $1 billion, a turnover of at most 25% and reports in each
of the four quarters before. Their best ideas (manager_best_ideas: new or half-again
positions among the ten largest) flag a company for the *next* quarter end, when the 13F
is public, and the outcome runs four quarters from there (one quarter secondary). The
same signal from every filer is the baseline the screen is judged against. Prices,
peers, controls and statistics are H008's.
"""

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import population, research
from fin_intel.models import Issuer, ManagerBestIdea, ManagerQuarter, QuarterlyPrice
from fin_intel.studies import h008

HYPOTHESIS = "H010"
MIN_POSITIONS, MAX_POSITIONS = 10, 40
MIN_VALUE = 1e9
HISTORY = 4  # quarters of reports before the signal quarter
MAX_TURNOVER = 0.25
HORIZONS = (4, 1)
# Entry quarter ends (the quarter after the signal quarter, when the holdings are public).
PERIODS = {
    "design": (date(2014, 9, 30), date(2018, 3, 31)),
    "validation": (date(2018, 6, 30), date(2021, 9, 30)),
    "holdout": (date(2021, 12, 31), date(2100, 1, 1)),
}
FROZEN = {
    "signal": "best_idea",
    "horizon": 4,
    "positions": [MIN_POSITIONS, MAX_POSITIONS],
    "min_value": MIN_VALUE,
    "history": HISTORY,
    "max_turnover": MAX_TURNOVER,
}

Book = tuple[int, float, float | None]  # positions, value, turnover


def next_quarter_end(period: date) -> date:
    first = date(
        period.year + (period.month == 12), 1 if period.month == 12 else period.month + 1, 1
    )
    return h008.quarter_end(first + timedelta(days=45))


def eligible(books: dict[int, dict[date, Book]], filer: int, q: date) -> bool:
    by_q = books.get(filer, {})
    book = by_q.get(q)
    if book is None:
        return False
    positions, value, turnover = book
    if not MIN_POSITIONS <= positions <= MAX_POSITIONS or value < MIN_VALUE:
        return False
    if turnover is None or turnover > MAX_TURNOVER:
        return False
    earlier = sorted(d for d in by_q if d < q)
    return len(earlier) >= HISTORY and all(d > q - timedelta(days=380) for d in earlier[-HISTORY:])


def build_flags(
    books: dict[int, dict[date, Book]],
    ideas: list[tuple[int, date, str]],
    cusip_cik: dict[tuple[str, date], int],
) -> dict[tuple[int, date], h008.Flag]:
    """Companies flagged at the entry quarter end (the one after the signal quarter):
    `best_idea` by eligible managers, `consensus` when two or more, `any_manager` by any."""
    flags: dict[tuple[int, date], h008.Flag] = defaultdict(lambda: defaultdict(set))
    for filer, q, cusip in ideas:
        cik = cusip_cik.get((cusip, q))
        if cik is None:
            continue
        flag = flags[cik, next_quarter_end(q)]
        flag["any_manager"].add(str(filer))
        if eligible(books, filer, q):
            flag["best_idea"].add(str(filer))
    for flag in flags.values():
        if len(flag.get("best_idea", ())) >= 2:
            flag["consensus"] = set(flag["best_idea"])
    return flags


def analyze(
    flags: dict[tuple[int, date], h008.Flag],
    prices: dict[int, dict[date, h008.Price]],
    splits: dict[int, list[tuple[date, float]]],
    sic: dict[int, int | None],
    members: set[int],
    events: dict[int, list[date]],
    window: tuple[date, date],
    reps: int = 1000,
) -> dict[str, Any]:
    flags = {k: f for k, f in flags.items() if window[0] <= k[1] <= window[1]}
    flagged = sum(1 for f in flags.values() if f.get("best_idea"))
    priced = sum(
        1 for (cik, q), f in flags.items() if f.get("best_idea") and q in prices.get(cik, {})
    )
    result: dict[str, Any] = {
        "flagged_company_quarters": flagged,
        "with_price": priced,
        "coverage": priced / flagged if flagged else None,
        "managers_eligible_per_quarter": None,
    }
    for horizon in HORIZONS:
        quarters = [q for q in h008.build_quarters(prices, splits, sic, members, horizon)
                    if window[0] <= q.end <= window[1]]  # fmt: skip
        lags = horizon - 1
        best = h008.portfolio(quarters, flags, lambda f: bool(f.get("best_idea")), lags)
        consensus = h008.portfolio(quarters, flags, lambda f: bool(f.get("consensus")), lags)
        anyone = h008.portfolio(quarters, flags, lambda f: bool(f.get("any_manager")), lags)
        block: dict[str, Any] = {
            "quarters_available": len(quarters),
            "best_idea": best,
            "consensus": consensus,
            "any_manager": anyone,
            "best_minus_any": h008.difference(best, anyone, lags),
            "exits": h008.exits(quarters, prices, flags, events, kind="best_idea"),
        }
        if horizon == 4:
            block["after_controls"] = h008.after_controls(quarters, flags, reps, kind="best_idea")
        for d in (best, consensus, anyone):
            d.pop("series", None)
        result[f"h{horizon}"] = block
    result["verdict"] = verdict(result)
    return result


def verdict(result: dict[str, Any]) -> str:
    h4 = result.get("h4", {})
    best, anyone, controls = (
        h4.get("best_idea", {}),
        h4.get("any_manager", {}),
        h4.get("after_controls", {}),
    )
    if "mean_excess" not in best:
        return "too few quarters"
    beats_any = "mean_excess" not in anyone or best["mean_excess"] > anyone["mean_excess"]
    strong = best["mean_excess"] >= 0.02 and (best["t"] or 0) >= 2
    if strong and beats_any and controls.get("adjusted_mean", 0) > 0:
        return "support"
    if best["mean_excess"] <= 0 or not beats_any:
        return "rejection"
    return "weak"


# --- data ------------------------------------------------------------------------------


def load_books(session: Session, end: date) -> dict[int, dict[date, Book]]:
    out: dict[int, dict[date, Book]] = defaultdict(dict)
    for filer, period, positions, value, turnover in session.execute(
        select(
            ManagerQuarter.filer_cik,
            ManagerQuarter.period,
            ManagerQuarter.positions,
            ManagerQuarter.value,
            ManagerQuarter.turnover,
        ).where(ManagerQuarter.period <= end)
    ):
        out[filer][period] = (positions, value, turnover)
    return out


def load_ideas(session: Session, end: date) -> list[tuple[int, date, str]]:
    return [
        (filer, period, cusip)
        for filer, period, cusip in session.execute(
            select(ManagerBestIdea.filer_cik, ManagerBestIdea.period, ManagerBestIdea.cusip).where(
                ManagerBestIdea.period >= h008.PRICES_FROM, ManagerBestIdea.period <= end
            )
        )
    ]


def load_cusip_ciks(session: Session, end: date) -> dict[tuple[str, date], int]:
    return {
        (cusip, period): cik
        for cusip, period, cik in session.execute(
            select(QuarterlyPrice.cusip, QuarterlyPrice.period, QuarterlyPrice.cik).where(
                QuarterlyPrice.cik.is_not(None),
                QuarterlyPrice.period >= h008.PRICES_FROM,
                QuarterlyPrice.period <= end,
            )
        )
    }


def run(session: Session, period: str, log: bool = True) -> list[dict[str, Any]]:
    if period not in PERIODS:
        raise ValueError(f"period is one of {tuple(PERIODS)}")
    if period == "holdout" and not research.holdout_allowed(session, HYPOTHESIS, FROZEN):
        raise research.HoldoutError("freeze the variant first (study H010 --freeze), once")
    window = PERIODS[period]
    end = research.HOLDOUT_START - timedelta(days=1) if period != "holdout" else date.today()
    prices = h008.load_prices(session, end)
    if period != "holdout":
        research.check_period(period, max(q for by_q in prices.values() for q in by_q))
    books = load_books(session, end)
    flags = build_flags(books, load_ideas(session, end), load_cusip_ciks(session, end))
    sic = dict(session.execute(select(Issuer.cik, Issuer.sic)).all())
    result = analyze(
        flags, prices, h008.load_splits(session), sic, population.members(session),
        h008.load_exit_events(session), window,
    )  # fmt: skip
    quarters = sorted(
        {
            q
            for by_q in books.values()
            for q in by_q
            if window[0] <= next_quarter_end(q) <= window[1]
        }
    )
    per_quarter = [sum(1 for f in books if eligible(books, f, q)) for q in quarters]
    result["managers_eligible_per_quarter"] = (
        sum(per_quarter) / len(per_quarter) if per_quarter else None
    )
    result["period"] = period
    if log:
        research.log(session, HYPOTHESIS, FROZEN, period, result)
    return [result]
