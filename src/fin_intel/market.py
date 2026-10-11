"""US listings and market data: security lists (SEC, Massive, Tiingo), daily bars, splits and
dividends, ADR share counts. Market-wide rows resolve symbols as of their date."""

import logging
from collections import Counter, defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import CorporateAction, DailyBar, Issuer, Security, TickerHistory
from fin_intel.pipeline import tracked, utc_today
from fin_intel.providers import (
    MassiveProvider,
    ProviderError,
    SecProvider,
    TiingoProvider,
    massive,
    sec,
    tiingo,
)
from fin_intel.raw import RawStore
from fin_intel.registry import Dataset
from fin_intel.symbols import by_symbol, get_security, record_tickers

log = logging.getLogger(__name__)


def load_company_tickers(session: Session, payload: Any, today: date) -> int:
    """Reconcile our securities with SEC's current ticker list.

    - Rename: a CIK's old ticker vanished and exactly one new ticker appeared for it
      (FB -> META); the existing security takes the new ticker and keeps its history.
    - Reuse: a ticker now belongs to a different CIK; the old holder gives it up and a new
      security is created, so the two companies' price histories never merge.
    - Delisting: an SEC-origin security missing from the list is marked inactive but keeps
      its ticker until someone else takes it.
    Securities other sources created are left alone unless SEC lists their ticker, in which
    case SEC claims them.
    """
    incoming: dict[str, dict] = {}
    for row in sec.parse_company_tickers(payload):
        if row["ticker"]:
            incoming[row["ticker"].upper()] = row  # the SEC file can repeat a ticker
    issuers = {r["cik"]: {"cik": r["cik"], "name": r["name"]} for r in incoming.values()}
    upsert(session, Issuer, issuers.values(), key=["cik"])

    securities = list(session.scalars(select(Security)))
    by_ticker = {s.ticker: s for s in securities if s.ticker}
    # Securities with a CIK that no longer hold a listed ticker: candidates for a rename.
    unlisted: dict[int, list[Security]] = defaultdict(list)

    for ticker, row in incoming.items():
        holder = by_ticker.get(ticker)
        if holder is not None and holder.cik is not None and holder.cik != row["cik"]:
            log.warning("%s moved from CIK %s to CIK %s", ticker, holder.cik, row["cik"])
            holder.ticker = None
            del by_ticker[ticker]
            unlisted[holder.cik].append(holder)
    session.flush()  # release reused tickers before anyone takes them
    for s in by_ticker.values():
        if s.origin == "sec" and s.cik is not None and s.ticker not in incoming:
            unlisted[s.cik].append(s)

    new_tickers_per_cik = Counter(r["cik"] for t, r in incoming.items() if t not in by_ticker)
    for ticker, row in incoming.items():
        security = by_ticker.get(ticker)
        if security is None:
            candidates = unlisted.get(row["cik"], [])
            if len(candidates) == 1 and new_tickers_per_cik[row["cik"]] == 1:
                security = candidates.pop()
                log.info("%s renamed to %s", security.ticker, ticker)
                by_ticker.pop(security.ticker, None)
                security.ticker = ticker
            else:
                security = Security(ticker=ticker)
                session.add(security)
            by_ticker[ticker] = security
        security.name, security.exchange, security.cik = row["name"], row["exchange"], row["cik"]
        security.active = True
        security.origin = "sec"  # SEC's list is the authority for what it lists

    # Securities other sources created (ETFs from Massive, ...) are theirs to deactivate.
    for s in securities:
        if s.origin == "sec" and s.cik is not None and s.ticker not in incoming:
            s.active = False
    session.flush()
    record_tickers(session, [by_ticker[t] for t in incoming], today)
    return len(incoming)


def load_tiingo_metadata(session: Session, ticker: str, payload: Any, today: date) -> int:
    """Create a security for a symbol no reference list has given us yet."""
    if get_security(session, ticker) is not None:
        return 0
    meta = tiingo.parse_metadata(payload)
    security = Security(
        ticker=meta["ticker"], name=meta["name"], exchange=meta["exchange"], origin="tiingo"
    )
    session.add(security)
    session.flush()
    record_tickers(session, [security], today)
    return 1


MASSIVE_ORIGINS = {"stocks": "massive", "otc": "massive-otc"}


def load_massive_tickers(session: Session, market: str, payload: Any, today: date) -> int:
    """Add and enrich securities from one page of Massive's reference tickers.

    - Every listed security gets Massive's type, FIGIs and primary exchange.
    - Unknown symbols (ETFs, funds, notes SEC doesn't list) become securities with
      origin "massive"; known ones keep their SEC name and CIK.
    - Identity is the composite FIGI, which survives ticker changes. For securities Massive
      created, a known FIGI under a new symbol is a rename, and a held symbol arriving with
      a different FIGI is a reuse (the old holder gives the symbol up). Securities SEC lists
      are never renamed or released by Massive: another symbol for their FIGI becomes an
      alias in their ticker history (VSEE -> VSEED, CSAN -> CSANY).
    Deactivation needs the whole list, so it is a separate step (deactivate_unseen_massive).
    Securities created from the OTC list have origin "massive-otc"; the main list promotes
    them to "massive" if they uplist.
    """
    origin = MASSIVE_ORIGINS[market]
    rows = massive.parse_tickers(payload)
    securities = list(session.scalars(select(Security)))
    by_ticker = {s.ticker: s for s in securities if s.ticker}
    by_figi = {s.figi: s for s in securities if s.figi}
    seen, aliases = [], []
    for row in rows:
        symbol, figi = row["symbol"], row["figi"]
        security = by_ticker.get(symbol)
        if security is not None and figi and security.figi and security.figi != figi:
            if not _massive_owned(security):
                # SEC lists this symbol for another instrument; SEC decides.
                log.info(
                    "%s: Massive FIGI %s differs from %s; skipped", symbol, figi, security.figi
                )
                continue
            log.warning("%s moved from FIGI %s to %s", symbol, security.figi, figi)
            security.ticker, security.active = None, False
            del by_ticker[symbol]
            session.flush()  # release the symbol before anyone takes it
            security = None
        if security is not None and row["cik"] and security.cik and security.cik != row["cik"]:
            # Same symbol, issuer attributed differently (e.g. a preferred issued by a
            # subsidiary). SEC is the authority on CIKs; the type and FIGIs still apply.
            log.info("%s: Massive CIK %s, SEC CIK %s; kept SEC's", symbol, row["cik"], security.cik)
        if security is None and figi and (same := by_figi.get(figi)) is not None:
            if not _massive_owned(same):
                # A security SEC lists, shown by Massive under another symbol: a temporary
                # one (VSEE -> VSEED after a reverse split), an OTC line (CSAN -> CSANY) or
                # a rename SEC hasn't caught up with. Record an alias; SEC keeps the ticker.
                aliases.append((same, symbol))
                continue
            log.info("%s renamed to %s (FIGI %s)", same.ticker, symbol, figi)
            by_ticker.pop(same.ticker, None)
            same.ticker = symbol
            security = same
        if security is None:
            security = Security(ticker=symbol, name=row["name"], origin=origin)
            session.add(security)
        by_ticker[symbol] = security
        if figi:
            by_figi[figi] = security

        if row["cik"] and security.cik is None:
            upsert(session, Issuer, [{"cik": row["cik"], "name": None}], key=["cik"], update=[])
            security.cik = row["cik"]
        security.name = security.name or row["name"]
        security.security_type = row["security_type"]
        security.figi = figi or security.figi
        security.share_class_figi = row["share_class_figi"] or security.share_class_figi
        security.mic = row["mic"]
        if security.origin in MASSIVE_ORIGINS.values():
            if origin == "massive":
                security.origin = origin
            security.active = True
        seen.append(security)
    session.flush()
    record_tickers(session, seen, today)
    upsert(
        session,
        TickerHistory,
        [
            {"security_id": sec.id, "ticker": sym, "first_seen": today, "last_seen": today}
            for sec, sym in aliases
        ],
        key=["security_id", "ticker"],
        update=["last_seen"],
    )
    return len(seen)


def _massive_owned(security: Security) -> bool:
    """Created from Massive's lists: Massive may rename it or give its symbol up."""
    return security.origin.startswith("massive")


def deactivate_unseen_massive(session: Session, market: str, as_of: date) -> int:
    """Deactivate securities a Massive list created but hasn't listed since `as_of`."""
    last_seen = (
        select(func.max(TickerHistory.last_seen))
        .where(TickerHistory.security_id == Security.id)
        .scalar_subquery()
    )
    stale = session.scalars(
        select(Security).where(
            Security.origin == MASSIVE_ORIGINS[market],
            Security.active,
            func.coalesce(last_seen, date.min) < as_of,
        )
    ).all()
    for security in stale:
        security.active = False
    return len(stale)


def load_massive_delisted(session: Session, payload: Any) -> int:
    """Record delisted securities, so history isn't limited to today's survivors.

    For each delisted listing (symbol S, last traded around date d), in order:
    1. Same composite FIGI as a security we know: it's that security, under an old symbol
       (a rename, recorded in its ticker history) or now delisted.
    2. S's current holder has the same CIK (or either identifier is missing): that
       company has just delisted; its data stays put.
    3. Otherwise S was reused: a separate delisted security takes S's history up to d,
       and rows already attached to the new holder for those dates move to it.
    Delisted securities keep `ticker` NULL; S is in their ticker history through d.
    """
    rows = massive.parse_delisted(payload)
    securities = list(session.scalars(select(Security)))
    by_ticker = {s.ticker: s for s in securities if s.ticker}
    by_figi = {s.figi: s for s in securities if s.figi}
    known = set(
        session.execute(
            select(TickerHistory.ticker, Security.delisted_on)
            .join(Security, Security.id == TickerHistory.security_id)
            .where(Security.ticker.is_(None), Security.delisted_on.is_not(None))
        ).all()
    )
    history, repairs, count = [], [], 0
    for row in rows:
        symbol, delisted_on, figi, cik = row["symbol"], row["delisted_on"], row["figi"], row["cik"]
        if (symbol, delisted_on) in known:
            continue  # imported on an earlier sync
        if figi and (same := by_figi.get(figi)) is not None:
            if same.ticker == symbol:
                _mark_delisted(same, delisted_on)
            else:  # an old symbol of a renamed security
                history.append((same, symbol, delisted_on))
            continue
        holder = by_ticker.get(symbol)
        if holder is not None and _same_company(holder, cik, figi):
            _mark_delisted(holder, delisted_on)
            holder.figi = holder.figi or figi
            continue
        if cik:
            upsert(session, Issuer, [{"cik": cik, "name": None}], key=["cik"], update=[])
        security = Security(
            ticker=None,
            name=row["name"],
            origin="massive-delisted",
            active=False,
            security_type=row["security_type"],
            mic=row["mic"],
            cik=cik,
            figi=figi,
            share_class_figi=row["share_class_figi"],
            delisted_on=delisted_on,
        )
        session.add(security)
        history.append((security, symbol, delisted_on))
        known.add((symbol, delisted_on))
        if figi:
            by_figi[figi] = security
        if holder is not None:
            repairs.append((holder.id, delisted_on))
        count += 1
    session.flush()
    upsert(
        session,
        TickerHistory,
        [
            {"security_id": s.id, "ticker": t, "first_seen": d, "last_seen": d}
            for s, t, d in history
        ],
        key=["security_id", "ticker"],
        update=["last_seen"],
    )
    # Market rows for a reused symbol were attached to its current holder; those dated up
    # to the old listing's delisting belong to the delisted security (backfilled from raw).
    for holder_id, until in repairs:
        session.execute(
            delete(DailyBar).where(
                DailyBar.security_id == holder_id,
                DailyBar.source == "massive",
                DailyBar.date <= until,
            )
        )
        session.execute(
            delete(CorporateAction).where(
                CorporateAction.security_id == holder_id,
                CorporateAction.source == "massive",
                CorporateAction.ex_date <= until,
            )
        )
    return count


def _mark_delisted(security: Security, delisted_on: date) -> None:
    security.delisted_on = delisted_on
    if security.origin.startswith("massive"):  # SEC decides for securities it lists
        security.active = False


def load_massive_ticker_details(session: Session, ticker: str, payload: Any, today: date) -> int:
    """A listing's shares outstanding (for an ADR, in depositary shares)."""
    info = payload.get("results") or {}
    shares = info.get("share_class_shares_outstanding")
    figi = info.get("composite_figi")
    security = (
        session.scalar(select(Security).where(Security.figi == figi)) if figi else None
    ) or get_security(session, massive.normalize_symbol(ticker))
    if security is None or not shares:
        return 0
    security.shares_outstanding = float(shares)
    security.shares_as_of = today
    return 1


def _same_company(holder: Security, cik: int | None, figi: str | None) -> bool:
    """No identifier contradicts it. Missing identifiers count as a match: this never
    moves data on a guess."""
    return not (cik and holder.cik and holder.cik != cik) and not (
        figi and holder.figi and holder.figi != figi
    )


def load_tiingo_daily(session: Session, ticker: str, payload: Any) -> int:
    security = get_security(session, ticker)
    if security is None:
        raise ProviderError(f"{ticker}: unknown security; load its metadata first")
    bars, actions = tiingo.parse_daily(payload)
    for row in bars + actions:
        row["security_id"] = security.id
    upsert(session, CorporateAction, actions, key=["security_id", "ex_date", "action", "source"])
    return upsert(session, DailyBar, bars, key=["security_id", "date", "source"])


def load_massive_grouped_daily(session: Session, day: str, payload: Any) -> int:
    rows = by_symbol(session, massive.parse_grouped_daily(date.fromisoformat(day), payload), "date")
    return upsert(session, DailyBar, rows, key=["security_id", "date", "source"])


def load_massive_actions(session: Session, dataset: str, payload: Any) -> int:
    parse = massive.parse_splits if dataset == "splits" else massive.parse_dividends
    rows = by_symbol(session, parse(payload), "ex_date")
    return upsert(
        session, CorporateAction, rows, key=["security_id", "ex_date", "action", "source"]
    )


def backfill_from_raw(session: Session, store: RawStore | None, symbols: set[str]) -> int:
    """Reload stored market-wide rows for newly discovered symbols, without the network.

    Grouped daily bars, splits and dividends were filtered to known symbols when first
    loaded. When a symbol gains a (security, symbol) pairing (a new security, a delisted
    listing, a rename) its rows are replayed from those same raw responses, and the
    date-aware resolver assigns each to the right security, so the live database matches
    what a rebuild would produce.
    """
    if store is None or not symbols:
        return 0
    records = list(
        store.records(
            provider="massive",
            datasets=[n for p, n in registry.where(symbol_keyed=True) if p == "massive"],
            connection=session.connection(),
        )
    )
    latest = {(r.dataset, r.key): r.id for r in records}
    snapshots = registry.where(snapshot=True)
    loaded = 0
    for r in records:
        if (r.provider, r.dataset) in snapshots and latest[(r.dataset, r.key)] != r.id:
            continue
        payload = r.json()
        rows = [
            row
            for row in payload.get("results") or []
            if massive.normalize_symbol(row.get("T") or row.get("ticker") or "") in symbols
        ]
        if rows:
            loaded += registry.get(r.provider, r.dataset).load(
                session, r.key, {**payload, "results": rows}, r.fetched_at
            )
    log.info("backfilled %d rows for %d newly paired symbols", loaded, len(symbols))
    return loaded


@contextmanager
def discovering(session: Session, store: RawStore | None) -> Generator[None]:
    """Backfill stored market data for symbols that gained a (security, symbol) pairing
    inside the block: new securities, delisted listings and renames."""

    def pairs() -> set[tuple[int, str]]:
        return set(session.execute(select(TickerHistory.security_id, TickerHistory.ticker)).all())

    before = pairs()
    yield
    session.flush()
    backfill_from_raw(session, store, {symbol for _, symbol in pairs() - before})


def sync_tickers(session: Session, sec_provider: SecProvider) -> int:
    with tracked(session, "sec", "company_tickers", "all") as result:
        payload = sec_provider.fetch_company_tickers()
        with discovering(session, sec_provider.raw_store):
            result["rows"] = load_company_tickers(session, payload, utc_today())
    return result["rows"]


def sync_prices(
    session: Session, tiingo_provider: TiingoProvider, ticker: str, start: date | None = None
) -> int:
    """Incrementally sync unadjusted bars and corporate actions. Stored history never needs
    refetching: adjustments are computed on read."""
    ticker = ticker.upper()
    with tracked(session, "tiingo", "daily_prices", ticker) as result:
        # Fetch everything before writing anything: the raw store commits each response in
        # its own transaction, which must not interleave with this session's writes.
        security = get_security(session, ticker)
        metadata = None
        if security is None:
            metadata = tiingo_provider.fetch_metadata(ticker)
            symbol, last_stored = tiingo.parse_metadata(metadata)["ticker"], None
        elif security.ticker is None:
            raise ProviderError(f"{ticker}: symbol now belongs to another security")
        else:
            symbol = security.ticker
            last_stored = session.scalar(
                select(func.max(DailyBar.date)).where(
                    DailyBar.security_id == security.id, DailyBar.source == tiingo_provider.name
                )
            )
        # Refetch the last stored day too, so a late correction to it is picked up.
        payload = tiingo_provider.fetch_daily(symbol, start=start or last_stored)

        if metadata is not None:
            load_tiingo_metadata(session, symbol, metadata, utc_today())
        result["rows"] = load_tiingo_daily(session, symbol, payload)
    return result["rows"]


def sync_reference_tickers(
    session: Session, massive_provider: MassiveProvider, market: str = "stocks"
) -> int:
    """Massive's full active ticker list for a market: add, enrich, then deactivate the
    Massive-origin securities it no longer lists. For stocks, also record securities
    delisted within our market history, and backfill their stored bars."""
    with tracked(session, "massive", "tickers", market) as result:
        pages = massive_provider.fetch_tickers(market)
        delisted = []
        if market == "stocks":
            # Delistings since our market history begins (Massive's free plan: 2 years).
            first_bar = session.scalar(
                select(func.min(DailyBar.date)).where(DailyBar.source == "massive")
            )
            delisted = massive_provider.fetch_delisted(
                first_bar or utc_today() - timedelta(days=730)
            )
        today = utc_today()
        with discovering(session, massive_provider.raw_store):
            result["rows"] = sum(load_massive_tickers(session, market, p, today) for p in pages)
            for page in delisted:
                load_massive_delisted(session, page)
        deactivate_unseen_massive(session, market, today)
    return result["rows"]


def due_listing_shares(session: Session, max_age_days: int = 28, limit: int = 150) -> list[str]:
    """ADRs (primary listings) whose depositary share count is missing or stale, oldest
    first. Each run refreshes at most `limit` (Massive's free plan: 5 calls a minute)."""
    from fin_intel import metrics

    cutoff = utc_today() - timedelta(days=max_age_days)
    due = [
        s
        for s in metrics.primary_securities(session).values()
        if s.security_type == "ADRC" and s.ticker and (s.shares_as_of or date.min) < cutoff
    ]
    due.sort(key=lambda s: s.shares_as_of or date.min)
    return [s.ticker for s in due[:limit] if s.ticker]


def sync_listing_shares(session: Session, massive_provider: MassiveProvider, ticker: str) -> int:
    with tracked(session, "massive", "ticker_details", ticker) as result:
        session.commit()  # fetch-then-load
        payload = massive_provider.fetch_ticker_details(ticker.replace("-", "."))
        result["rows"] = load_massive_ticker_details(session, ticker, payload, utc_today())
    return result["rows"]


def sync_market_daily(
    session: Session, massive_provider: MassiveProvider, day: date, include_otc: bool = False
) -> int:
    """Unadjusted bars for every known security on one trading day, in one call."""
    with tracked(session, "massive", "grouped_daily", day.isoformat()) as result:
        payload = massive_provider.fetch_grouped_daily(day, include_otc=include_otc)
        result["rows"] = load_massive_grouped_daily(session, day.isoformat(), payload)
    return result["rows"]


def sync_market_actions(
    session: Session, massive_provider: MassiveProvider, dataset: str, since: date
) -> int:
    """Market-wide splits or dividends with ex-dates on or after `since`."""
    fetch = (
        massive_provider.fetch_splits if dataset == "splits" else massive_provider.fetch_dividends
    )
    with tracked(session, "massive", dataset, "all") as result:
        pages = fetch(since)
        result["rows"] = sum(load_massive_actions(session, dataset, page) for page in pages)
    return result["rows"]


registry.register(
    Dataset(
        "sec",
        "company_tickers",
        lambda s, k, p, t: load_company_tickers(s, p, t.date()),
        ("market",),
        reference=True,
    ),
    Dataset(
        "tiingo",
        "metadata",
        lambda s, k, p, t: load_tiingo_metadata(s, k, p, t.date()),
        ("prices", "market"),
        reference=True,
    ),
    Dataset(
        "tiingo",
        "daily_prices",
        lambda s, k, p, t: load_tiingo_daily(s, k, p),
        ("prices", "market"),
    ),
    Dataset(
        "massive",
        "tickers",
        lambda s, k, p, t: load_massive_tickers(s, k, p, t.date()),
        ("market",),
        reference=True,
    ),
    Dataset(
        "massive",
        "delisted",
        lambda s, k, p, t: load_massive_delisted(s, p),
        ("market",),
        reference=True,
    ),
    Dataset(
        "massive",
        "grouped_daily",
        lambda s, k, p, t: load_massive_grouped_daily(s, k, p),
        ("prices", "market"),
        snapshot=True,
        symbol_keyed=True,
    ),
    Dataset(
        "massive",
        "splits",
        lambda s, k, p, t: load_massive_actions(s, "splits", p),
        ("prices", "market"),
        symbol_keyed=True,
    ),
    Dataset(
        "massive",
        "dividends",
        lambda s, k, p, t: load_massive_actions(s, "dividends", p),
        ("prices", "market"),
        symbol_keyed=True,
    ),
    Dataset(
        "massive",
        "ticker_details",
        lambda s, k, p, t: load_massive_ticker_details(s, k, p, t.date()),
        ("market",),
        snapshot=True,
    ),
)
