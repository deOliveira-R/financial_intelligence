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
    BINARY_DATASETS,
    LOADERS,
    REFERENCE_DATASETS,
    REQUEST_DATASETS,
    SNAPSHOT_DATASETS,
    deactivate_unseen_massive,
    get_security,
)
from fin_intel.models import (
    CompanyMetrics,
    Concept,
    CongressReport,
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
    InsiderTransaction,
    InstitutionalFiler,
    InstitutionalPosition,
    Issuer,
    PortfolioTransaction,
    Security,
    ShortInterest,
    StatementItem,
    TickerHistory,
)
from fin_intel.raw import RawStore

# Every dataset that writes facts: wiping facts means replaying all of them, whichever
# regulator they come from (issuer profiles first, for fiscal year ends).
FACT_DATASETS = [
    ("dart", "company"),
    ("sec", "companyfacts"),
    ("sec", "filing_xbrl"),
    ("sec", "submissions"),
    ("dart", "statements"),
    ("dart", "share_counts"),
    ("edinet", "instance"),
    ("twse", "table"),
    ("esef", "report"),
]

# target -> (datasets to replay, tables to wipe first, in foreign-key-safe order)
TARGETS = {
    "fundamentals": (
        FACT_DATASETS,
        [StatementItem, Fact, FiscalCalendar, Filing, Concept],
    ),
    "prices": (
        [
            ("tiingo", "metadata"),
            ("tiingo", "daily_prices"),
            ("massive", "grouped_daily"),
            ("massive", "splits"),
            ("massive", "dividends"),
            ("twse", "prices"),
        ],
        [DailyBar, CorporateAction],
    ),
    "insiders": (
        [("sec", "insider_dataset"), ("sec", "form4")],
        [InsiderTransaction],
    ),
    "holdings": (
        [("sec", "13f_dataset"), ("openfigi", "mapping")],
        [InstitutionalPosition, InstitutionalFiler, CusipMapping],
    ),
    "congress": (
        [("house", "fd_index"), ("house", "ptr"), ("senate", "search"), ("senate", "ptr")],
        [CongressTrade, CongressReport],
    ),
    "policy": ([("usaspending", "naics_month")], [FederalObligation]),
    "shorts": ([("finra", "short_interest")], [ShortInterest]),
    "funds": ([("sec", "nport")], [FundHolding]),
    "texts": ([("sec", "filing_document")], [FilingText]),
    "cot": (
        [("cftc", "legacy"), ("cftc", "disaggregated"), ("cftc", "tff")],
        [CotPosition],
    ),
    "events": (
        [("sec", "submissions"), ("sec", "submissions_page")],
        [CorporateEvent],
    ),
    "economic": (
        [
            ("fred", "series"),
            ("fred", "observations"),
            ("fred", "vintages"),
            ("eia", "series"),
            ("fred", "series_release"),
            ("fred", "release_dates"),
            ("fed", "fomc_calendar"),
            ("boj", "mpm_schedule"),
            ("mof", "jgb_curve"),
            ("mof", "flows"),
            ("cboe", "pc_archive"),
            ("cboe", "daily_options"),
        ],
        [
            EconomicVintage,
            EconomicObservation,
            EconomicSeries,
            EconomicReleaseDate,
            EconomicRelease,
        ],
    ),
    # Securities and market data, without re-loading fundamentals (minutes, not hours).
    "market": (
        [
            *REFERENCE_DATASETS,
            ("tiingo", "daily_prices"),
            ("massive", "grouped_daily"),
            ("massive", "splits"),
            ("massive", "dividends"),
            ("massive", "ticker_details"),
            ("twse", "prices"),
            ("openfigi", "listings"),
        ],
        [ForwardOutcome, CompanyMetrics, DailyBar, CorporateAction, TickerHistory, Security],
    ),
    # Everything: reference data in fetch order (so renames happen as they did), then the rest.
    "all": (
        list(LOADERS),
        [
            ForwardOutcome,
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
            EconomicReleaseDate,
            EconomicRelease,
            InsiderTransaction,
            InstitutionalPosition,
            InstitutionalFiler,
            CusipMapping,
            CongressTrade,
            CongressReport,
            CotPosition,
            CorporateEvent,
            FederalObligation,
            ShortInterest,
            FundHolding,
            FilingText,
        ],
    ),
}


def rebuild(session: Session, store: RawStore, target: str) -> Counter[str]:
    datasets, tables = TARGETS[target]
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
        if dataset in BINARY_DATASETS:
            payload = r.body
        elif dataset in REQUEST_DATASETS:
            payload = {"request": r.params.get("body"), "response": r.json()}
        else:
            payload = r.json()
        LOADERS[dataset](session, r.key, payload, r.fetched_at)
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
    if target in ("market", "all"):
        from fin_intel import crosslist

        crosslist.link(session)  # US listings of foreign issuers (needs securities and bars)
        session.commit()
    if target in ("market", "prices", "all"):
        # Derived from bars and statements: refresh now rather than at the next daily sync.
        breadth.compute(session)
        metrics.compute(session)
    return loaded
