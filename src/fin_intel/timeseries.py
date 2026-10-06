"""Aligned time series for research: prices, macro and indicators on one trading calendar.

A spec names one series:

    px:SPY                 total-return adjusted close (splits and dividends)
    close:SPY              raw close          volume:SPY   raw volume
    fred:DGS10             a FRED series, as known on each date (see `pit`)
    breadth:pct_above_200d market internals for US-listed common stocks (see breadth.py)
    eia:crude_stocks       EIA weekly energy data by ID or alias (see energy.py)
    cot:crude:managed_money:index  CFTC positioning: market:group[:field], field one of
                           net (default), long, short, net_pct_oi, oi, index (see cot.py)
    px:CPER/px:GLD         ratio of two series      fred:DGS10-fred:DGS2   difference
    px:SPY|sma:200         transforms, applied left to right after any ratio/difference:
                           sma:N ema:N rsi:N macd ret:N diff:N vol:N z:N high:N low:N dd:N yoy

Dates are the trading days on which SPY has a bar. Every transform is causal.

Point-in-time (`pit=True`, the default): a FRED value on day D is what had been published
by D, from the series' revision history (ALFRED vintages). Monthly CPI appears on its
release date, as first printed, and changes on the days it was revised. Before a series'
history begins (e.g. 2005 for daily Treasury yields) there's no value. With `pit=False`,
today's revised values are carried forward from each observation date, which looks
ahead by the publication lag and the later revisions. COT positions (Tuesday's) likewise
appear on their Friday release, and EIA weekly data on its Wednesday or Thursday release
(on the week's Friday with `pit=False`).
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel import indicators
from fin_intel.models import (
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicVintage,
    MarketBreadth,
    ShortInterest,
)
from fin_intel.prices import adjustments

SOURCES = (
    "px",
    "close",
    "volume",
    "fred",
    "breadth",
    "cot",
    "eia",
    "jp",
    "gov",
    "si",
    "cboe",
    "lobby",
)
_OPERATOR = re.compile(rf"([/-])(?=(?:{'|'.join(SOURCES)}):)")


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    terms: tuple[tuple[str, str], ...]  # ((source, id), ...)
    operator: str | None  # "/" or "-"
    transforms: tuple[tuple[str, int | None], ...]


def parse(text: str) -> Spec:
    head, *steps = text.strip().split("|")
    parts = _OPERATOR.split(head)
    if len(parts) not in (1, 3):
        raise SpecError(f"{text!r}: at most one ratio or difference per spec")
    terms = []
    for part in parts[::2]:
        source, _, ident = part.partition(":")
        if source not in SOURCES or not ident:
            raise SpecError(f"{part!r}: expected one of {', '.join(SOURCES)} followed by :id")
        terms.append((source, ident.upper()))
    transforms = []
    for step in steps:
        name, _, arg = step.partition(":")
        if name not in indicators.TRANSFORMS:
            raise SpecError(f"unknown transform {name!r}")
        _, windowed, default = indicators.TRANSFORMS[name]
        if windowed and not arg and default is None:
            raise SpecError(f"{name} needs a window, e.g. {name}:20")
        transforms.append((name, int(arg) if arg else default))
    return Spec(tuple(terms), parts[1] if len(parts) == 3 else None, tuple(transforms))


def calendar(session: Session, start: date | None, end: date | None) -> list[date]:
    """Trading days: dates on which SPY has a bar from any source."""
    from fin_intel.ingest import get_security

    spy = get_security(session, "SPY")
    if spy is None:
        raise SpecError("the trading calendar needs SPY prices; run sync-prices SPY")
    stmt = select(DailyBar.date).where(DailyBar.security_id == spy.id).distinct()
    if start:
        stmt = stmt.where(DailyBar.date >= start)
    if end:
        stmt = stmt.where(DailyBar.date <= end)
    return sorted(session.scalars(stmt))


def build(
    session: Session,
    specs: Sequence[str],
    start: date | None = None,
    end: date | None = None,
    pit: bool = True,
) -> tuple[list[date], dict[str, indicators.Series]]:
    """Evaluate specs on the trading calendar. Transforms need history before `start`, so
    each series is computed from the earliest data and cut to [start, end] at the end."""
    parsed = {text: parse(text) for text in specs}
    full = calendar(session, None, end)
    cache: dict[tuple[str, str], indicators.Series] = {}
    out = {}
    for text, spec in parsed.items():
        values = []
        for term in spec.terms:
            if term not in cache:
                cache[term] = _load(session, term, full, pit)
            values.append(cache[term])
        series = (
            values[0] if spec.operator is None else _combine(values[0], values[1], spec.operator)
        )
        for name, window in spec.transforms:
            fn, windowed, _ = indicators.TRANSFORMS[name]
            if name == "yoy":
                series = fn(series, full)
            else:
                series = fn(series, window) if windowed else fn(series)
        out[text] = series
    keep = [i for i, d in enumerate(full) if start is None or d >= start]
    return [full[i] for i in keep], {k: [v[i] for i in keep] for k, v in out.items()}


def _combine(a: indicators.Series, b: indicators.Series, op: str) -> indicators.Series:
    if op == "/":
        return [None if x is None or not y else x / y for x, y in zip(a, b, strict=True)]
    return [None if x is None or y is None else x - y for x, y in zip(a, b, strict=True)]


def _load(
    session: Session, term: tuple[str, str], days: list[date], pit: bool
) -> indicators.Series:
    source, ident = term
    if source == "eia":
        return _eia(session, ident, days, pit)
    if source == "jp":
        return _japan(session, ident, days, pit)
    if source == "gov":
        return _contracts(session, ident, days, pit)
    if source == "si":
        return _short_interest(session, ident, days, pit)
    if source == "cboe":
        return _cboe(session, ident, days, pit)
    if source == "lobby":
        return _lobbying(session, ident, days, pit)
    if source == "cot":
        return _cot(session, ident.lower(), days, pit)
    if source == "breadth":
        return _breadth(session, ident.lower(), days)
    if source == "fred":
        return _fred_pit(session, ident, days) if pit else _fred_latest(session, ident, days)
    return _price(session, ident, days, source)


def _price(session: Session, ticker: str, days: list[date], field: str) -> indicators.Series:
    """Bars merged across sources: the deepest-history source wins on each date, others fill
    its gaps (sources agree where both exist). Splits and dividends are merged the same
    way, and adjustments computed once over the merged bars."""
    from fin_intel.ingest import get_security

    security = get_security(session, ticker)
    if security is None:
        raise SpecError(f"unknown ticker {ticker}")
    return security_prices(session, security.id, days, field, label=ticker)


def security_prices(
    session: Session, security_id: int, days: list[date], field: str = "px", label: str = ""
) -> indicators.Series:
    """`_price` by security id (also reaches delisted securities, which have no ticker)."""
    bars = session.scalars(select(DailyBar).where(DailyBar.security_id == security_id)).all()
    if not bars:
        raise SpecError(f"no prices for {label or security_id}")
    first_date: dict[str, date] = {}
    for b in bars:
        first_date[b.source] = min(first_date.get(b.source, b.date), b.date)
    rank = {src: i for i, src in enumerate(sorted(first_date, key=first_date.get))}
    merged: dict[date, DailyBar] = {}
    for b in sorted(bars, key=lambda b: rank[b.source], reverse=True):
        merged[b.date] = b  # higher-priority sources overwrite
    series_bars = [merged[d] for d in sorted(merged)]
    split_days = session.scalars(
        select(CorporateAction.ex_date).where(
            CorporateAction.security_id == security_id, CorporateAction.action == "split"
        )
    ).all()
    series_bars = series_bars[_last_break(series_bars, split_days) :]
    if field == "px":
        actions: dict[tuple[date, str], tuple[int, float]] = {}
        for ex_date, action, value, src in session.execute(
            select(
                CorporateAction.ex_date,
                CorporateAction.action,
                CorporateAction.value,
                CorporateAction.source,
            ).where(CorporateAction.security_id == security_id)
        ):
            key = (ex_date, action)
            if src in rank and (key not in actions or rank[src] < actions[key][0]):
                actions[key] = (rank[src], value)
        factors = adjustments(series_bars, [(d, a, v) for (d, a), (_, v) in actions.items()])
        by_date = {
            b.date: b.close * factors[b.date].price for b in series_bars if b.close is not None
        }
    elif field == "close":
        by_date = {b.date: b.close for b in series_bars}
    else:
        by_date = {b.date: float(b.volume) if b.volume is not None else None for b in series_bars}
    return [by_date.get(d) for d in days]


JUMP, JUMP_FLOOR, JUMP_WINDOW, SPLIT_SLACK = 4.0, 1.0, 5, 7


def _last_break(bars: list[DailyBar], split_days: Sequence[date]) -> int:
    """Index where a series' history should start: after its last unexplained jump.

    A close at least JUMP times the day before, that holds (the median of the next
    JUMP_WINDOW closes vs the previous ones), above JUMP_FLOOR dollars, with no split within
    SPLIT_SLACK days, isn't a market move: it's an unadjusted reverse split or a company
    relisted after bankruptcy on the old one's history. Returns across it would be fiction,
    so the series starts after it. Falls aren't treated this way: real collapses happen
    and dropping them would bias research toward survivors.
    """
    closes = [b.close for b in bars]
    start = 0
    for i in range(1, len(closes)):
        prev, cur = closes[i - 1], closes[i]
        if not prev or not cur or cur < JUMP_FLOOR or cur / prev < JUMP:
            continue
        before = [c for c in closes[max(0, i - JUMP_WINDOW) : i] if c]
        after = [c for c in closes[i : i + JUMP_WINDOW] if c]
        if len(after) < JUMP_WINDOW or not before or median(after) / median(before) < JUMP:
            continue  # unconfirmed (too recent) or not held
        if any(abs((d - bars[i].date).days) <= SPLIT_SLACK for d in split_days):
            continue
        start = i
    return start


def _breadth(session: Session, ident: str, days: list[date]) -> indicators.Series:
    """`breadth:<field>` (the whole market) or `breadth:<universe>:<field>`, e.g.
    breadth:industry:semiconductors:pct_above_200d."""
    from fin_intel import breadth

    universe, _, field = ident.rpartition(":")
    universe = universe or "us_common"
    if field not in breadth.FIELDS:
        raise SpecError(f"unknown breadth field {field!r}; one of {', '.join(breadth.FIELDS)}")
    rows = session.scalars(select(MarketBreadth).where(MarketBreadth.universe == universe))
    by_date = {r.date: breadth.field_values(r, field) for r in rows}
    if not by_date:
        raise SpecError(f"no breadth data for {universe}; run fin-intel derive-breadth")
    return [by_date.get(d) for d in days]


def _eia(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    from fin_intel import energy

    try:
        series_id = energy.resolve(ident)
    except ValueError as exc:
        raise SpecError(str(exc)) from None
    rows = session.execute(
        select(EconomicObservation.date, EconomicObservation.value)
        .where(EconomicObservation.series_id == series_id, EconomicObservation.value.is_not(None))
        .order_by(EconomicObservation.date)
    ).all()
    if not rows:
        raise SpecError(f"no EIA data for {series_id}; run fin-intel sync-eia")
    known = [(energy.available_on(series_id, d) if pit else d, v) for d, v in rows]
    return _step(known, days)


def _japan(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    from fin_intel import japan

    try:
        series_id = japan.resolve(ident)
    except ValueError as exc:
        raise SpecError(str(exc)) from None
    rows = session.execute(
        select(EconomicObservation.date, EconomicObservation.value)
        .where(EconomicObservation.series_id == series_id, EconomicObservation.value.is_not(None))
        .order_by(EconomicObservation.date)
    ).all()
    if not rows:
        raise SpecError(f"no data for {series_id}; run fin-intel sync-japan")
    known = [(japan.available_on(series_id, d) if pit else d, v) for d, v in rows]
    return _step(known, days)


def _contracts(session: Session, prefix: str, days: list[date], pit: bool) -> indicators.Series:
    """Monthly federal contract obligations for NAICS codes starting with `prefix`."""
    from fin_intel import contracts

    if not prefix.isdigit():
        raise SpecError(f"gov:{prefix}: expected a NAICS code or prefix, e.g. gov:3364")
    rows = contracts.series(session, prefix)
    if not rows:
        raise SpecError(f"no federal obligations for NAICS {prefix}*; run sync-contracts")
    return _step([(contracts.available_on(m) if pit else m, v) for m, v in rows], days)


def _short_interest(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    """`si:GME` short position (shares), `si:GME:dtc` days to cover."""
    from fin_intel import shortinterest
    from fin_intel.ingest import get_security

    ticker, _, field = ident.partition(":")
    column = {"": ShortInterest.short_position, "DTC": ShortInterest.days_to_cover}.get(field)
    if column is None:
        raise SpecError(f"si:{ident}: field is empty (shares short) or dtc (days to cover)")
    security = get_security(session, ticker)
    if security is None:
        raise SpecError(f"unknown ticker {ticker}")
    rows = session.execute(
        select(ShortInterest.settlement_date, column)
        .where(ShortInterest.security_id == security.id, column.is_not(None))
        .order_by(ShortInterest.settlement_date)
    ).all()
    if not rows:
        raise SpecError(f"no short interest for {ticker}; run sync-short-interest")
    return _step([(shortinterest.available_on(d) if pit else d, v) for d, v in rows], days)


def _cboe(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    """`cboe:equity_pc`, `cboe:spx_puts`... (putcall.py)."""
    from fin_intel import putcall

    try:
        series_id = putcall.resolve(ident)
    except ValueError as exc:
        raise SpecError(str(exc)) from None
    rows = session.execute(
        select(EconomicObservation.date, EconomicObservation.value)
        .where(EconomicObservation.series_id == series_id, EconomicObservation.value.is_not(None))
        .order_by(EconomicObservation.date)
    ).all()
    if not rows:
        raise SpecError(f"no data for {series_id}; run fin-intel sync-cboe")
    return _step([(putcall.available_on(d) if pit else d, v) for d, v in rows], days)


def _lobbying(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    """`lobby:DEF` quarterly spend on an issue area, `lobby:DEF:count` its reports."""
    from fin_intel import lobbying

    code, _, field = ident.partition(":")
    if field not in ("", "COUNT"):
        raise SpecError(f"lobby:{ident}: field is empty (spend) or count")
    rows = lobbying.by_issue(session, code)
    if not rows:
        raise SpecError(f"no lobbying data for issue {code}; run fin-intel sync-lobbying")
    known = [
        (lobbying.available_on(y, q) if pit else lobbying.quarter_end(y, q), n if field else s)
        for y, q, s, n in rows
    ]
    return _step(known, days)


def _step(known: list[tuple[date, float]], days: list[date]) -> indicators.Series:
    """The latest value known on each day."""
    out, i, current = [], 0, None
    for d in days:
        while i < len(known) and known[i][0] <= d:
            current = known[i][1]
            i += 1
        out.append(current)
    return out


def _cot(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    from fin_intel import cot

    market, _, rest = ident.partition(":")
    group, _, field = rest.partition(":")
    if not group:
        raise SpecError(f"cot:{ident}: expected cot:market:group[:field]")
    try:
        return cot.series_by_day(session, market, group, field or "net", days, pit)
    except ValueError as exc:
        raise SpecError(str(exc)) from None


def _fred_latest(session: Session, series_id: str, days: list[date]) -> indicators.Series:
    rows = session.execute(
        select(EconomicObservation.date, EconomicObservation.value)
        .where(EconomicObservation.series_id == series_id, EconomicObservation.value.is_not(None))
        .order_by(EconomicObservation.date)
    ).all()
    if not rows:
        raise SpecError(f"no FRED data for {series_id}; run sync-economic {series_id}")
    out, i, current = [], 0, None
    for d in days:
        while i < len(rows) and rows[i].date <= d:
            current = rows[i].value
            i += 1
        out.append(current)
    return out


def _fred_pit(session: Session, series_id: str, days: list[date]) -> indicators.Series:
    """Value known on each day: the latest observation published by then, as published
    (its latest revision on or before that day)."""
    rows = session.execute(
        select(EconomicVintage.realtime_start, EconomicVintage.date, EconomicVintage.value)
        .where(EconomicVintage.series_id == series_id)
        .order_by(EconomicVintage.realtime_start, EconomicVintage.date)
    ).all()
    if not rows:
        raise SpecError(f"no revision history for {series_id}; run sync-economic {series_id}")
    known: dict[date, float | None] = {}
    latest: date | None = None  # most recent observation date with a value
    out, i = [], 0
    for d in days:
        while i < len(rows) and rows[i].realtime_start <= d:
            _, obs_date, value = rows[i]
            known[obs_date] = value
            if value is not None and (latest is None or obs_date >= latest):
                latest = obs_date
            elif value is None and obs_date == latest:  # withdrawn: fall back
                latest = max((k for k, v in known.items() if v is not None), default=None)
            i += 1
        out.append(known[latest] if latest is not None and latest <= d else None)
    return out
