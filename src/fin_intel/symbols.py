"""Securities by symbol: lookups, ticker history, and which security a symbol meant on a date."""

import logging
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import Security, TickerHistory

log = logging.getLogger(__name__)


def get_security(session: Session, ticker: str) -> Security | None:
    """The security currently using `ticker`, else the one that used it most recently."""
    ticker = ticker.upper()
    security = session.scalar(select(Security).where(Security.ticker == ticker))
    if security is None:
        security = session.scalar(
            select(Security)
            .join(TickerHistory, TickerHistory.security_id == Security.id)
            .where(TickerHistory.ticker == ticker)
            .order_by(TickerHistory.last_seen.desc())
        )
    return security


def record_tickers(session: Session, securities: list[Security], today: date) -> None:
    rows = [
        {"security_id": s.id, "ticker": s.ticker, "first_seen": today, "last_seen": today}
        for s in securities
        if s.ticker
    ]
    upsert(session, TickerHistory, rows, key=["security_id", "ticker"], update=["last_seen"])


class SymbolResolver:
    """Which security a symbol meant on a given date.

    A symbol can pass from one company to another, and market-wide feeds identify rows by
    symbol only. Past holders are known from ticker_history entries for symbols a security
    no longer uses (delisted securities, renames), valid through their last_seen date; on
    later dates the symbol means its current holder.
    """

    def __init__(self, session: Session):
        self.current = dict(
            session.execute(
                select(Security.ticker, Security.id).where(Security.ticker.is_not(None))
            ).all()
        )
        self.past: dict[str, list[tuple[date, int]]] = defaultdict(list)
        history = session.execute(
            select(
                TickerHistory.ticker,
                TickerHistory.first_seen,
                TickerHistory.last_seen,
                TickerHistory.security_id,
                Security.ticker,
            ).join(Security, Security.id == TickerHistory.security_id)
        ).all()
        first_as_current = {
            sid: first for symbol, first, _, sid, current in history if symbol == current
        }
        # Until when each current holder still traded under an earlier symbol (a rename,
        # not an alias: the old symbol was last seen before the current one first was).
        self.renamed_until: dict[int, date] = {}
        for symbol, _, last_seen, security_id, current in history:
            if symbol == current:
                continue
            self.past[symbol].append((last_seen, security_id))
            started = first_as_current.get(security_id)
            if started is not None and last_seen < started:
                previous = self.renamed_until.get(security_id, last_seen)
                self.renamed_until[security_id] = max(previous, last_seen)
        for listings in self.past.values():
            listings.sort()

    def resolve(self, symbol: str, on: date) -> int | None:
        past = self.past.get(symbol, ())
        for last_seen, security_id in past:
            if on <= last_seen:
                return security_id
        if symbol in self.current:
            holder = self.current[symbol]
            if on <= self.renamed_until.get(holder, date.min):
                # The holder still traded under its old symbol then: this symbol belonged to
                # another company we don't know (BNY was a muni fund while BNY Mellon was BK).
                return None
            return holder
        # No current holder: its most recent past holder (an alias still in use since the
        # last reference sync, or a delisted listing).
        return past[-1][1] if past else None


def by_symbol(
    session: Session, rows: list[dict[str, Any]], date_field: str
) -> list[dict[str, Any]]:
    """Attach security_id to market-wide rows by symbol as of each row's date; drop
    unknown symbols (warrants, units, OTC names we don't track)."""
    resolver = SymbolResolver(session)
    out = []
    for row in rows:
        security_id = resolver.resolve(row.pop("symbol"), row[date_field])
        if security_id is not None:
            out.append({**row, "security_id": security_id})
    if skipped := len(rows) - len(out):
        log.info("skipped %d of %d rows with unknown symbols", skipped, len(rows))
    return out
