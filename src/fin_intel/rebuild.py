"""Rebuild normalized tables from the raw layer, without touching the network.

Use after changing a parser or loader: wipe the affected tables and replay the stored
responses, in the order they were fetched, through the same loaders live syncs use.
"""

from collections import Counter
from datetime import datetime

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from fin_intel import breadth, metrics, registry
from fin_intel.db import Base
from fin_intel.market import deactivate_unseen_massive
from fin_intel.models import (
    Bill,
    BillSubject,
    Committee,
    CommitteeMembership,
    CompanyMetrics,
    Concept,
    Concordance,
    CongressReport,
    CongressReportMember,
    CongressTrade,
    CorporateAction,
    CorporateEvent,
    CotPosition,
    CusipMapping,
    DailyBar,
    EconomicObservation,
    EconomicRelease,
    EconomicReleaseDate,
    EconomicSeries,
    EconomicVintage,
    Fact,
    FederalObligation,
    Filing,
    FilingText,
    FiscalCalendar,
    ForwardOutcome,
    FundHolding,
    GraphEdge,
    GraphNode,
    InsiderTransaction,
    InstitutionalFiler,
    InstitutionalPosition,
    IoCode,
    IoNaics,
    IoUse,
    Issuer,
    LegislatorTerm,
    LobbyingIssue,
    LobbyingReport,
    PortfolioTransaction,
    PresidentialDocument,
    PresidentialText,
    Security,
    ShortInterest,
    StatementItem,
    TickerHistory,
)
from fin_intel.raw import RawStore
from fin_intel.symbols import get_security

# target -> tables it wipes before replaying its datasets (each dataset names its targets in
# its registry declaration). "fundamentals" replays every dataset that writes facts,
# whichever regulator they come from; "market" securities and market data without
# re-loading fundamentals (minutes, not hours).
TABLES: dict[str, list] = {
    "fundamentals": [StatementItem, Fact, FiscalCalendar, Filing, Concept],
    "prices": [DailyBar, CorporateAction],
    "insiders": [InsiderTransaction],
    "holdings": [InstitutionalPosition, InstitutionalFiler, CusipMapping],
    "congress": [
        CongressReportMember,
        CongressTrade,
        CongressReport,
        CommitteeMembership,
        Committee,
        LegislatorTerm,
    ],
    "policy": [FederalObligation],
    "shorts": [ShortInterest],
    "funds": [FundHolding],
    "lobbying": [LobbyingIssue, LobbyingReport],
    "bills": [BillSubject, Bill],
    "presidential": [PresidentialText, PresidentialDocument],
    "texts": [FilingText],
    "cot": [CotPosition],
    "events": [CorporateEvent],
    "economic": [
        EconomicVintage,
        EconomicObservation,
        EconomicSeries,
        EconomicReleaseDate,
        EconomicRelease,
    ],
    "industries": [GraphEdge, GraphNode, IoUse, IoNaics, IoCode, Concordance],
    "market": [ForwardOutcome, CompanyMetrics, DailyBar, CorporateAction, TickerHistory, Security],
}
# Everything, children before parents: every table above plus issuers.
_ALL = {t for tables in TABLES.values() for t in tables} | {Issuer}
_ORDER = {table: i for i, table in enumerate(Base.metadata.sorted_tables)}
TABLES["all"] = sorted(_ALL, key=lambda m: _ORDER[m.__table__], reverse=True)
TARGETS = list(TABLES)


def _restart_ids(session: Session, tables: list) -> None:
    """Number a wiped table's rows from where its surviving rows end (1 when empty), as a
    fresh load would: ids are referenced (facts by filing, portfolios by security), so a
    rebuild must reproduce them."""
    for table in tables:
        key = list(table.__table__.primary_key)
        if len(key) != 1 or key[0].type.python_type is not int:
            continue
        name, column = table.__tablename__, key[0].name
        # pg_get_serial_sequence is NULL for keys without a sequence: setval then does nothing.
        session.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{name}', '{column}'), "
                f"COALESCE((SELECT MAX({column}) FROM {name}), 0) + 1, false)"
            )
        )


def rebuild(session: Session, store: RawStore, target: str) -> Counter[str]:
    datasets, tables = registry.in_target(target), TABLES[target]
    # Read the raw index before writing anything (bodies load lazily, one at a time).
    records = [
        r
        for r in store.records(connection=session.connection())
        if (r.provider, r.dataset) in datasets
    ]
    wipes_securities = Security in tables
    if wipes_securities:
        # Portfolio transactions point at securities; detach, re-attach by symbol after.
        session.execute(update(PortfolioTransaction).values(security_id=None))
    for table in tables:
        session.execute(delete(table))
    _restart_ids(session, tables)

    # Reference datasets first (in fetch order), so market data fetched before a security
    # was discovered still finds it: the result is independent of the order syncs ran in.
    references = registry.where(reference=True)
    snapshots = registry.where(snapshot=True)
    records.sort(key=lambda r: (r.provider, r.dataset) not in references)
    # Snapshot datasets (each response is complete) only need their latest response.
    latest = {(r.provider, r.dataset, r.key): r.id for r in records}
    loaded: Counter[str] = Counter()
    for r in records:
        dataset = (r.provider, r.dataset)
        if dataset in snapshots and latest[(r.provider, r.dataset, r.key)] != r.id:
            continue
        declared = registry.get(*dataset)
        if declared.payload == "bytes":
            payload = r.body
        elif declared.payload == "request":
            payload = {"request": r.params.get("body"), "response": r.json()}
        else:
            payload = r.json()
        declared.load(session, r.key, payload, r.fetched_at)
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
    if target in ("congress", "all"):
        from fin_intel import congress_members

        congress_members.link(session)  # reports to members (needs both loaded)
        session.commit()
    if target in ("industries", "all"):
        from fin_intel import graph

        graph.build(session)  # derived from the IO tables and the themes
        session.commit()
    if target in ("market", "all"):
        from fin_intel import crosslist

        crosslist.link(session)  # US listings of foreign issuers (needs securities and bars)
        session.commit()
    if target in ("market", "prices", "all"):
        # Derived from bars and statements: refresh now rather than at the next daily sync.
        breadth.compute_all(session)
        metrics.compute(session)
    return loaded
