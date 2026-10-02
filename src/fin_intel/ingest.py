"""Load provider payloads into the normalized tables.

Live syncs fetch everything an item needs first and only then load it, because the raw
store commits each response in its own transaction (so it survives a failed load).

Each (provider, dataset) has one loader taking the raw payload. Live syncs fetch (which
records the raw response) and then call the loader; `rebuild.py` replays stored raw
responses through the same loaders. The API only ever reads what these have stored.
"""

import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import derive
from fin_intel.db import upsert
from fin_intel.models import (
    Concept,
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    Fact,
    Filing,
    Issuer,
    Security,
    SyncState,
    TickerHistory,
)
from fin_intel.providers import (
    FredProvider,
    MassiveProvider,
    ProviderError,
    SecProvider,
    TiingoProvider,
    fred,
    massive,
    sec,
    tiingo,
)
from fin_intel.raw import RawStore

log = logging.getLogger(__name__)


# --- securities ------------------------------------------------------------------------


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
    - Identity is the composite FIGI, which survives ticker changes: a known FIGI under a
      new symbol is a rename; a held symbol arriving with a different FIGI is a reuse, so
      the old holder gives the symbol up.
    Deactivation needs the whole list, so it is a separate step (deactivate_unseen_massive).
    Securities created from the OTC list have origin "massive-otc"; the main list promotes
    them to "massive" if they uplist.
    """
    origin = MASSIVE_ORIGINS[market]
    rows = massive.parse_tickers(payload)
    securities = list(session.scalars(select(Security)))
    by_ticker = {s.ticker: s for s in securities if s.ticker}
    by_figi = {s.figi: s for s in securities if s.figi}
    seen = []
    for row in rows:
        symbol, figi = row["symbol"], row["figi"]
        security = by_ticker.get(symbol)
        if security is not None and figi and security.figi and security.figi != figi:
            log.warning("%s moved from FIGI %s to %s", symbol, security.figi, figi)
            security.ticker, security.active = None, False
            del by_ticker[symbol]
            session.flush()  # release the symbol before anyone takes it
            security = None
        if security is not None and row["cik"] and security.cik and security.cik != row["cik"]:
            # Same symbol, issuer attributed differently (e.g. a preferred issued by a
            # subsidiary). SEC is the authority on CIKs; the type and FIGIs still apply.
            log.info("%s: Massive CIK %s, SEC CIK %s; kept SEC's", symbol, row["cik"], security.cik)
        if security is None and figi and (moved := by_figi.get(figi)) is not None:
            log.info("%s renamed to %s (FIGI %s)", moved.ticker, symbol, figi)
            by_ticker.pop(moved.ticker, None)
            moved.ticker = symbol
            security = moved
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
    return len(seen)


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


# --- prices ----------------------------------------------------------------------------


def load_tiingo_daily(session: Session, ticker: str, payload: Any) -> int:
    security = get_security(session, ticker)
    if security is None:
        raise ProviderError(f"{ticker}: unknown security; load its metadata first")
    bars, actions = tiingo.parse_daily(payload)
    for row in bars + actions:
        row["security_id"] = security.id
    upsert(session, CorporateAction, actions, key=["security_id", "ex_date", "action", "source"])
    return upsert(session, DailyBar, bars, key=["security_id", "date", "source"])


def _by_symbol(session: Session, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach security_id to market-wide rows by current ticker; drop unknown symbols.

    Market-wide feeds list warrants, units and OTC names we don't track; run sync-tickers
    first so every SEC-listed security is known.
    """
    ids = dict(
        session.execute(
            select(Security.ticker, Security.id).where(Security.ticker.is_not(None))
        ).all()
    )
    out = []
    for row in rows:
        security_id = ids.get(row.pop("symbol"))
        if security_id is not None:
            out.append({**row, "security_id": security_id})
    if skipped := len(rows) - len(out):
        log.info("skipped %d of %d rows with unknown symbols", skipped, len(rows))
    return out


def load_massive_grouped_daily(session: Session, day: str, payload: Any) -> int:
    rows = _by_symbol(session, massive.parse_grouped_daily(date.fromisoformat(day), payload))
    return upsert(session, DailyBar, rows, key=["security_id", "date", "source"])


def load_massive_actions(session: Session, dataset: str, payload: Any) -> int:
    parse = massive.parse_splits if dataset == "splits" else massive.parse_dividends
    rows = _by_symbol(session, parse(payload))
    return upsert(
        session, CorporateAction, rows, key=["security_id", "ex_date", "action", "source"]
    )


# --- fundamentals ----------------------------------------------------------------------


def load_company_facts(session: Session, cik: int, payload: Any) -> int:
    filings, concepts, facts = sec.parse_company_facts(cik, payload)
    upsert(session, Issuer, [{"cik": cik, "name": payload.get("entityName")}], key=["cik"])
    upsert(session, Filing, filings, key=["accession"])
    upsert(session, Concept, concepts, key=["taxonomy", "name"])

    filing_ids = dict(
        session.execute(select(Filing.accession, Filing.id).where(Filing.cik == cik)).all()
    )
    concept_ids = {
        (t, n): i for t, n, i in session.execute(select(Concept.taxonomy, Concept.name, Concept.id))
    }
    rows = [
        {
            "filing_id": filing_ids[f["accession"]],
            "concept_id": concept_ids[f["concept"]],
            "unit": f["unit"],
            "period_start": f["period_start"],
            "period_end": f["period_end"],
            "instant": f["instant"],
            "value": f["value"],
            "frame": f["frame"],
            "cik": cik,
        }
        for f in facts
    ]
    count = upsert(
        session,
        Fact,
        rows,
        key=["filing_id", "concept_id", "unit", "period_start", "period_end"],
    )
    derive.derive_issuer(session, cik)
    return count


# --- economic data ---------------------------------------------------------------------


def load_fred_series(session: Session, payload: Any) -> int:
    return upsert(session, EconomicSeries, [fred.parse_series(payload)], key=["id"])


def load_fred_observations(session: Session, series_id: str, payload: Any) -> int:
    rows = fred.parse_observations(series_id, payload)
    return upsert(session, EconomicObservation, rows, key=["series_id", "date"])


# --- loader registry (used by rebuild) -------------------------------------------------

# (provider, dataset) -> loader(session, key, payload, fetched_at)
Loader = Callable[[Session, Any, Any, datetime], int]
LOADERS: dict[tuple[str, str], Loader] = {
    ("sec", "company_tickers"): lambda s, k, p, t: load_company_tickers(s, p, t.date()),
    ("sec", "companyfacts"): lambda s, k, p, t: load_company_facts(s, int(k), p),
    ("tiingo", "metadata"): lambda s, k, p, t: load_tiingo_metadata(s, k, p, t.date()),
    ("tiingo", "daily_prices"): lambda s, k, p, t: load_tiingo_daily(s, k, p),
    ("massive", "tickers"): lambda s, k, p, t: load_massive_tickers(s, k, p, t.date()),
    ("massive", "grouped_daily"): lambda s, k, p, t: load_massive_grouped_daily(s, k, p),
    ("massive", "splits"): lambda s, k, p, t: load_massive_actions(s, "splits", p),
    ("massive", "dividends"): lambda s, k, p, t: load_massive_actions(s, "dividends", p),
    ("fred", "series"): lambda s, k, p, t: load_fred_series(s, p),
    ("fred", "observations"): lambda s, k, p, t: load_fred_observations(s, k, p),
}
# Datasets that create or rename securities. Rebuilds replay them before everything else,
# so symbol-keyed market data always resolves against the full security list.
REFERENCE_DATASETS = [("sec", "company_tickers"), ("massive", "tickers"), ("tiingo", "metadata")]
# Market-wide datasets keyed by symbol: rows for unknown symbols are skipped at load time,
# so they are replayed for securities discovered later (backfill_from_raw).
SYMBOL_KEYED_DATASETS = [
    ("massive", "grouped_daily"),
    ("massive", "splits"),
    ("massive", "dividends"),
]
# Datasets where each response is a full snapshot, so only the latest one matters.
SNAPSHOT_DATASETS = {
    ("sec", "companyfacts"),
    ("massive", "grouped_daily"),  # one complete response per trading day
    ("fred", "series"),
    ("fred", "observations"),
}


# --- live syncs ------------------------------------------------------------------------


def _today() -> date:
    """UTC, like raw fetched_at: a rebuild must stamp the same dates a live sync did."""
    return datetime.now(UTC).date()


@contextmanager
def tracked(session: Session, provider: str, dataset: str, key: str) -> Generator[dict[str, Any]]:
    """Record the outcome of one sync item in sync_state, committing or rolling back."""
    now = datetime.now(UTC)
    result: dict[str, Any] = {"rows": None}
    try:
        yield result
    except Exception as exc:
        session.rollback()
        state = {"last_attempt": now, "last_error": str(exc)[:1000]}
        upsert(
            session,
            SyncState,
            [{"provider": provider, "dataset": dataset, "key": key, **state}],
            key=["provider", "dataset", "key"],
        )
        session.commit()
        raise
    state = {"last_attempt": now, "last_success": now, "last_error": None, "rows": result["rows"]}
    upsert(
        session,
        SyncState,
        [{"provider": provider, "dataset": dataset, "key": key, **state}],
        key=["provider", "dataset", "key"],
    )
    session.commit()


def backfill_from_raw(session: Session, store: RawStore | None, security_ids: set[int]) -> int:
    """Load stored market-wide rows for newly discovered securities, without the network.

    Grouped daily bars, splits and dividends were filtered to known symbols when first
    loaded; a security discovered later gets its rows from those same raw responses, so
    the live database matches what a rebuild would produce.
    """
    if store is None or not security_ids:
        return 0
    symbols = set(
        session.scalars(select(Security.ticker).where(Security.id.in_(security_ids))).all()
    ) - {None}
    records = list(
        store.records(
            provider="massive",
            datasets=[d for _, d in SYMBOL_KEYED_DATASETS],
            connection=session.connection(),
        )
    )
    latest = {(r.dataset, r.key): r.id for r in records}
    loaded = 0
    for r in records:
        if (r.provider, r.dataset) in SNAPSHOT_DATASETS and latest[(r.dataset, r.key)] != r.id:
            continue
        payload = r.json()
        rows = [
            row
            for row in payload.get("results") or []
            if massive.normalize_symbol(row.get("T") or row.get("ticker") or "") in symbols
        ]
        if rows:
            loaded += LOADERS[(r.provider, r.dataset)](
                session, r.key, {**payload, "results": rows}, r.fetched_at
            )
    log.info("backfilled %d rows for %d new securities", loaded, len(symbols))
    return loaded


@contextmanager
def discovering(session: Session, store: RawStore | None) -> Generator[None]:
    """Backfill stored market data for any security created inside the block."""
    before = set(session.scalars(select(Security.id)))
    yield
    session.flush()
    backfill_from_raw(session, store, set(session.scalars(select(Security.id))) - before)


def sync_tickers(session: Session, sec_provider: SecProvider) -> int:
    with tracked(session, "sec", "company_tickers", "all") as result:
        payload = sec_provider.fetch_company_tickers()
        with discovering(session, sec_provider.raw_store):
            result["rows"] = load_company_tickers(session, payload, _today())
    return result["rows"]


def sync_fundamentals(session: Session, sec_provider: SecProvider, ticker: str) -> int:
    security = get_security(session, ticker)
    if security is None or security.cik is None:
        raise ProviderError(f"{ticker}: no CIK known; run sync-tickers first")
    with tracked(session, "sec", "companyfacts", str(security.cik)) as result:
        payload = sec_provider.fetch_company_facts(security.cik)
        result["rows"] = load_company_facts(session, security.cik, payload)
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
            load_tiingo_metadata(session, symbol, metadata, _today())
        result["rows"] = load_tiingo_daily(session, symbol, payload)
    return result["rows"]


def sync_reference_tickers(
    session: Session, massive_provider: MassiveProvider, market: str = "stocks"
) -> int:
    """Massive's full active ticker list for a market: add, enrich, then deactivate the
    Massive-origin securities it no longer lists."""
    with tracked(session, "massive", "tickers", market) as result:
        pages = massive_provider.fetch_tickers(market)
        today = _today()
        with discovering(session, massive_provider.raw_store):
            result["rows"] = sum(load_massive_tickers(session, market, p, today) for p in pages)
        deactivate_unseen_massive(session, market, today)
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


def sync_economic(session: Session, fred_provider: FredProvider, series_id: str) -> int:
    """Full refetch each time: one call returns the whole series and picks up revisions."""
    with tracked(session, "fred", "observations", series_id) as result:
        series = fred_provider.fetch_series(series_id)
        observations = fred_provider.fetch_observations(series_id)
        load_fred_series(session, series)
        result["rows"] = load_fred_observations(session, series_id, observations)
    return result["rows"]
