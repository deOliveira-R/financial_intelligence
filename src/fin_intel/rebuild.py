"""Rebuild normalized tables from the raw layer, without touching the network.

Use after changing a parser or loader: wipe the affected tables and replay the stored
responses, in the order they were fetched, through the same loaders live syncs use.
"""

from collections import Counter

from sqlalchemy import delete
from sqlalchemy.orm import Session

from fin_intel.ingest import LOADERS, SNAPSHOT_DATASETS
from fin_intel.models import (
    Concept,
    CorporateAction,
    DailyBar,
    EconomicObservation,
    EconomicSeries,
    Fact,
    Filing,
    FiscalCalendar,
    Issuer,
    Security,
    TickerHistory,
)
from fin_intel.raw import RawStore

# target -> (datasets to replay, tables to wipe first, in foreign-key-safe order)
TARGETS = {
    "fundamentals": (
        [("sec", "companyfacts")],
        [Fact, FiscalCalendar, Filing, Concept],
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
        [("fred", "series"), ("fred", "observations")],
        [EconomicObservation, EconomicSeries],
    ),
    # Everything, replayed in global fetch order so ticker renames happen as they did.
    "all": (
        list(LOADERS),
        [
            Fact,
            FiscalCalendar,
            Filing,
            Concept,
            DailyBar,
            CorporateAction,
            TickerHistory,
            Security,
            Issuer,
            EconomicObservation,
            EconomicSeries,
        ],
    ),
}


def rebuild(session: Session, store: RawStore, target: str) -> Counter[str]:
    datasets, tables = TARGETS[target]
    for table in tables:
        session.execute(delete(table))

    records = [r for r in store.records() if (r.provider, r.dataset) in datasets]
    # Snapshot datasets (each response is complete) only need their latest response.
    latest = {(r.provider, r.dataset, r.key): r.id for r in records}
    loaded: Counter[str] = Counter()
    for r in records:
        dataset = (r.provider, r.dataset)
        if dataset in SNAPSHOT_DATASETS and latest[(r.provider, r.dataset, r.key)] != r.id:
            continue
        LOADERS[dataset](session, r.key, r.json(), r.fetched_at)
        loaded[f"{r.provider}/{r.dataset}"] += 1
    session.commit()
    return loaded
