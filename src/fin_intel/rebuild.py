"""Rebuild normalized tables from the raw layer, without touching the network.

Use after changing a parser or loader: wipe the affected tables and replay the stored
responses, in the order they were fetched, through the same loaders live syncs use.
"""

from collections import Counter
from datetime import datetime

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from fin_intel import breadth, metrics
from fin_intel.ingest import (
    LOADERS,
    REFERENCE_DATASETS,
    SNAPSHOT_DATASETS,
    deactivate_unseen_massive,
    get_security,
)
from fin_intel.models import (
    CompanyMetrics,
    Concept,
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    EconomicVintage,
    Fact,
    Filing,
    FiscalCalendar,
    Issuer,
    PortfolioTransaction,
    Security,
    StatementItem,
    TickerHistory,
)
from fin_intel.raw import RawStore

# target -> (datasets to replay, tables to wipe first, in foreign-key-safe order)
TARGETS = {
    "fundamentals": (
        [("sec", "companyfacts")],
        [StatementItem, Fact, FiscalCalendar, Filing, Concept],
    ),
    "prices": (
        [
            ("tiingo", "metadata"),
            ("tiingo", "daily_prices"),
            ("massive", "grouped_daily"),
            ("massive", "splits"),
            ("massive", "dividends"),
        ],
        [DailyBar, CorporateAction],
    ),
    "economic": (
        [("fred", "series"), ("fred", "observations"), ("fred", "vintages")],
        [EconomicVintage, EconomicObservation, EconomicSeries],
    ),
    # Securities and market data, without re-loading fundamentals (minutes, not hours).
    "market": (
        [
            *REFERENCE_DATASETS,
            ("tiingo", "daily_prices"),
            ("massive", "grouped_daily"),
            ("massive", "splits"),
            ("massive", "dividends"),
        ],
        [CompanyMetrics, DailyBar, CorporateAction, TickerHistory, Security],
    ),
    # Everything: reference data in fetch order (so renames happen as they did), then the rest.
    "all": (
        list(LOADERS),
        [
            CompanyMetrics,
            StatementItem,
            Fact,
            FiscalCalendar,
            Filing,
            Concept,
            DailyBar,
            CorporateAction,
            TickerHistory,
            Security,
            Issuer,
            EconomicVintage,
            EconomicObservation,
            EconomicSeries,
        ],
    ),
}


def rebuild(session: Session, store: RawStore, target: str) -> Counter[str]:
    datasets, tables = TARGETS[target]
    wipes_securities = Security in tables
    if wipes_securities:
        # Portfolio transactions point at securities; detach, re-attach by symbol after.
        session.execute(update(PortfolioTransaction).values(security_id=None))
    for table in tables:
        session.execute(delete(table))

    records = [r for r in store.records() if (r.provider, r.dataset) in datasets]
    # Reference datasets first (in fetch order), so market data fetched before a security
    # was discovered still finds it: the result is independent of the order syncs ran in.
    records.sort(key=lambda r: (r.provider, r.dataset) not in REFERENCE_DATASETS)
    # Snapshot datasets (each response is complete) only need their latest response.
    latest = {(r.provider, r.dataset, r.key): r.id for r in records}
    loaded: Counter[str] = Counter()
    for r in records:
        dataset = (r.provider, r.dataset)
        if dataset in SNAPSHOT_DATASETS and latest[(r.provider, r.dataset, r.key)] != r.id:
            continue
        LOADERS[dataset](session, r.key, r.json(), r.fetched_at)
        loaded[f"{r.provider}/{r.dataset}"] += 1
    # Deactivation needs a complete list, which page-by-page replay doesn't see; apply it
    # as of each market's latest reference sync, as the live sync did.
    latest_ticker_sync: dict[str, datetime] = {}
    for r in records:
        if (r.provider, r.dataset) == ("massive", "tickers"):
            latest_ticker_sync[r.key] = r.fetched_at
    for market, fetched_at in latest_ticker_sync.items():
        deactivate_unseen_massive(session, market, fetched_at.date())
    if wipes_securities:
        for tx in session.scalars(
            select(PortfolioTransaction).where(PortfolioTransaction.symbol.is_not(None))
        ):
            security = get_security(session, tx.symbol)
            tx.security_id = security.id if security else None
    session.commit()
    if target in ("market", "prices", "all"):
        # Derived from bars and statements: refresh now rather than at the next daily sync.
        breadth.compute(session)
        metrics.compute(session)
    return loaded
