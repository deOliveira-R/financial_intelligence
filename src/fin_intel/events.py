"""Event studies: do stocks outperform after a disclosed trade by insiders, members of
Congress or big managers?

Each event is dated when it became public (its filing date), and entered at the close of
the next trading day: filings often land after the close, so the filing day's close
wasn't tradable. Returns are total-return (adjusted) closes, and excess returns are
against SPY over the same days.
"""

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import timeseries
from fin_intel.models import (
    CongressReport,
    CongressTrade,
    CusipMapping,
    InsiderTransaction,
    InstitutionalPosition,
    Security,
)

HORIZONS = (5, 21, 63, 126)  # trading days: a week, a month, a quarter, half a year


@dataclass(frozen=True)
class Event:
    ticker: str
    known_on: date  # public from this day
    label: str = ""


# --- event sources ---------------------------------------------------------------------


def insider_clusters(
    session: Session, days: int = 30, min_insiders: int = 3, since: date | None = None
) -> list[Event]:
    """Open-market purchases (P, not 10b5-1) by `min_insiders` distinct insiders within
    `days`, dated at the filing that completed the cluster. One event per issuer per window."""
    stmt = select(InsiderTransaction).where(
        InsiderTransaction.trans_code == "P",
        InsiderTransaction.plan_10b5_1.is_(False),
        InsiderTransaction.filing_date.is_not(None),
        InsiderTransaction.issuer_symbol.is_not(None),
    )
    if since:
        stmt = stmt.where(InsiderTransaction.trans_date >= since)
    by_issuer: dict[int, list[InsiderTransaction]] = defaultdict(list)
    for t in session.scalars(stmt):
        by_issuer[t.issuer_cik].append(t)
    out = []
    for txs in by_issuer.values():
        txs.sort(key=lambda t: (t.filing_date, t.trans_date))
        last_event: date | None = None
        for i, t in enumerate(txs):
            assert t.filing_date is not None  # filtered above
            if last_event and t.filing_date < last_event + timedelta(days=days):
                continue
            window = [u for u in txs[: i + 1] if u.trans_date > t.trans_date - timedelta(days=days)]
            insiders = {u.owner_cik or u.owner_name for u in window}
            if len(insiders) >= min_insiders:
                last_event = t.filing_date
                out.append(Event(t.issuer_symbol or "", t.filing_date, f"{len(insiders)} insiders"))
    return out


def congress_purchases(
    session: Session, min_amount: float = 0, member: str | None = None
) -> list[Event]:
    """Stock purchases reported by members of Congress, dated at the report's filing."""
    stmt = (
        select(CongressTrade.ticker, CongressReport.filed, CongressReport.name)
        .join(CongressReport, CongressReport.doc_id == CongressTrade.doc_id)
        .where(
            CongressTrade.trans_type == "purchase",
            CongressTrade.ticker.is_not(None),
            CongressReport.filed.is_not(None),
        )
    )
    if min_amount > 0:
        stmt = stmt.where(CongressTrade.amount_min >= min_amount)
    if member:
        stmt = stmt.where(CongressReport.name.ilike(f"%{member}%"))
    seen = set()
    out = []
    for ticker, filed, name in session.execute(stmt):
        if ticker and filed and (ticker, filed) not in seen:  # one per stock per report day
            seen.add((ticker, filed))
            out.append(Event(ticker, filed, name or ""))
    return out


def new_positions(session: Session, filer_cik: int | None = None) -> list[Event]:
    """Positions a 13F filer didn't hold the previous quarter, dated at the filing."""
    stmt = (
        select(
            InstitutionalPosition.filer_cik,
            InstitutionalPosition.period,
            InstitutionalPosition.cusip,
            InstitutionalPosition.filed,
            Security.ticker,
        )
        .join(CusipMapping, CusipMapping.cusip == InstitutionalPosition.cusip)
        .join(Security, Security.id == CusipMapping.security_id)
        .where(InstitutionalPosition.put_call == "", Security.ticker.is_not(None))
    )
    if filer_cik:
        stmt = stmt.where(InstitutionalPosition.filer_cik == filer_cik)
    held: dict[tuple[int, date], set[str]] = defaultdict(set)
    rows = session.execute(stmt).all()
    for cik, period, cusip, _, _ in rows:
        held[(cik, period)].add(cusip)
    periods: dict[int, list[date]] = defaultdict(list)
    for cik, period in held:
        periods[cik].append(period)
    previous = {}
    for cik, ps in periods.items():
        ps.sort()
        for a, b in zip(ps, ps[1:], strict=False):
            previous[(cik, b)] = a
    out = []
    for cik, period, cusip, filed, ticker in rows:
        before = previous.get((cik, period))
        if before is None or not filed or not ticker or cusip in held[(cik, before)]:
            continue  # first quarter on record, or already held
        out.append(Event(ticker, filed, str(cik)))
    return out


def from_source(
    session: Session,
    source: str,
    member: str | None = None,
    min_amount: float = 0,
    cik: int | None = None,
    min_insiders: int = 3,
) -> list[Event]:
    if source == "insiders":
        return insider_clusters(session, min_insiders=min_insiders)
    if source == "congress":
        return congress_purchases(session, min_amount, member)
    if source == "13f":
        return new_positions(session, cik)
    raise ValueError(f"unknown event source {source!r}; one of insiders, congress, 13f")


# --- the study -------------------------------------------------------------------------


@dataclass(frozen=True)
class HorizonStats:
    horizon: int
    n: int
    mean_excess: float | None
    median_excess: float | None
    hit_rate: float | None  # share of events beating SPY
    t_stat: float | None
    mean_return: float | None


@dataclass
class Study:
    events: int
    priced: int  # events with prices to measure
    unpriced: list[str] = field(default_factory=list)  # tickers without prices
    horizons: list[HorizonStats] = field(default_factory=list)


def study(session: Session, events: Iterable[Event], horizons: tuple[int, ...] = HORIZONS) -> Study:
    events = list(events)
    days = timeseries.calendar(session, None, None)
    index = {d: i for i, d in enumerate(days)}
    spy = timeseries._price(session, "SPY", days, "px")
    prices: dict[str, list[float | None] | None] = {}
    excess: dict[int, list[float]] = {h: [] for h in horizons}
    raw: dict[int, list[float]] = {h: [] for h in horizons}
    priced, unpriced = 0, set()
    for e in events:
        if e.ticker not in prices:
            try:
                prices[e.ticker] = timeseries._price(session, e.ticker, days, "px")
            except timeseries.SpecError:
                prices[e.ticker] = None
        px = prices[e.ticker]
        if px is None:
            unpriced.add(e.ticker)
            continue
        # The first trading day after the event became public.
        start = next((index[d] for d in _after(e.known_on, 10) if d in index), None)
        p0, s0 = (px[start], spy[start]) if start is not None else (None, None)
        if start is None or p0 is None or s0 is None:
            continue
        priced += 1
        for h in horizons:
            end = start + h
            p1, s1 = (px[end], spy[end]) if end < len(days) else (None, None)
            if p1 is None or s1 is None:
                continue
            r = p1 / p0 - 1
            raw[h].append(r)
            excess[h].append(r - (s1 / s0 - 1))
    return Study(
        events=len(events),
        priced=priced,
        unpriced=sorted(unpriced),
        horizons=[_summarize(h, excess[h], raw[h]) for h in horizons],
    )


def _after(day: date, n: int) -> list[date]:
    return [day + timedelta(days=i) for i in range(1, n + 1)]


def _summarize(h: int, excess: list[float], raw: list[float]) -> HorizonStats:
    n = len(excess)
    if not n:
        return HorizonStats(h, 0, None, None, None, None, None)
    mean = sum(excess) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in excess) / (n - 1)) if n > 1 else 0.0
    return HorizonStats(
        horizon=h,
        n=n,
        mean_excess=mean,
        median_excess=median(excess),
        hit_rate=sum(1 for x in excess if x > 0) / n,
        t_stat=mean / (sd / math.sqrt(n)) if sd else None,
        mean_return=sum(raw) / n,
    )
