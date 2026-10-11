"""The data catalog: what each feed holds, when it last synced, and which feeds need a look.

Investing here is meant to be low-attention, with alerts only on exceptions, and a feed
that silently stops is the first exception to catch (FRED answered HTTP 500 for 70 series
on 2026-10-09 and nothing said so). Freshness comes from `sync_state`: a sync that finds
nothing new still records a success, where raw fetches would look stale for quarterly
sources. Each monitored feed has the most days its latest success may age (`EXPECTED`,
following the schedules in cli/ops.py); a feed is

- `stale` when its latest success is older than that,
- `failing` when more of its items are failing than a few stragglers (some items fail for
  good: a scanned PDF, a withdrawn filing),
- `ok` otherwise, or `unmonitored` when it runs on demand.

`fin-intel catalog` prints it (`--check` exits non-zero on any alert; sync-daily ends with
it), `/catalog` serves it.

Each feed also carries its provider's terms (`TERMS`), to know which features would need a
licensed source before anything built on them is shared or sold.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case, func, select, text
from sqlalchemy.orm import Session

from fin_intel import rebuild, registry
from fin_intel.models import RawResponse, SyncState

DAILY, WEEKLY, MONTHLY = 4, 9, 35  # days, with room for weekends and holidays
EXPECTED: dict[tuple[str, str], int] = {
    # sync-daily (Mon-Fri)
    ("massive", "grouped_daily"): DAILY,
    ("massive", "splits"): DAILY,
    ("massive", "dividends"): DAILY,
    ("sec", "insider_day"): DAILY,
    ("house", "fd_index"): DAILY,
    ("senate", "search"): DAILY,
    ("fred", "observations"): DAILY,
    ("eia", "series"): WEEKLY,  # weekly data
    ("mof", "jgb_curve"): DAILY,
    ("cboe", "daily_options"): DAILY,
    ("dart", "report"): DAILY,
    ("edinet", "documents"): DAILY,
    ("twse", "prices"): DAILY,
    # sync-weekly (Sunday)
    ("sec", "company_tickers"): WEEKLY,
    ("massive", "tickers"): WEEKLY,
    ("sec", "13f_dataset"): WEEKLY,
    ("cftc", "legacy"): WEEKLY,
    ("cftc", "disaggregated"): WEEKLY,
    ("cftc", "tff"): WEEKLY,
    ("sec", "events"): WEEKLY,
    ("sec", "xbrl_gaps"): WEEKLY,
    ("sec", "filing_text"): WEEKLY,
    ("twse", "table"): WEEKLY,
    ("esef", "report"): WEEKLY,
    ("fred", "release_dates"): WEEKLY,
    ("usaspending", "naics_month"): WEEKLY,
    ("finra", "short_interest"): WEEKLY,
    ("sec", "nport"): WEEKLY,
    ("legislators", "file"): WEEKLY,
    ("lda", "filings"): WEEKLY,
    ("govinfo", "billstatus"): WEEKLY,
    ("federalregister", "presidential_list"): WEEKLY,
    # checked monthly (yearly releases)
    ("bea", "supply_use"): MONTHLY,
    ("census", "concordance"): MONTHLY,
}
# Failing items tolerated before a feed counts as failing: at least this many, or this share.
STRAGGLERS, STRAGGLER_SHARE = 2, 0.01

# Provider terms, coarsely: "public" (a government or regulator's publication), "personal use"
# (a commercial provider's free plan), "check terms" (a third party's site or mixed rights:
# some FRED series, such as the ICE BofA spreads, are copyrighted by their source).
PUBLIC, PERSONAL, CHECK = "public", "personal use", "check terms"
TERMS: dict[str, str] = {
    **dict.fromkeys(
        (
            "sec",
            "bea",
            "census",
            "cftc",
            "eia",
            "fed",
            "govinfo",
            "federalregister",
            "usaspending",
            "house",
            "senate",
            "lda",
            "legislators",
            "edinet",
            "dart",
            "twse",
            "mof",
            "boj",
        ),
        PUBLIC,
    ),  # fmt: skip
    "massive": PERSONAL,
    "tiingo": PERSONAL,
    **dict.fromkeys(("fred", "cboe", "finra", "openfigi", "esef"), CHECK),
}


@dataclass
class Feed:
    provider: str
    dataset: str
    items: int
    failing: int
    last_success: datetime | None
    last_error: str | None
    expected_days: int | None
    status: str  # ok, stale, failing, unmonitored
    tables: list[str]
    raw_responses: int
    last_fetch: datetime | None
    terms: str | None = None

    @property
    def age_days(self) -> float | None:
        if self.last_success is None:
            return None
        return (datetime.now(UTC) - _utc(self.last_success)).total_seconds() / 86400


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _status(items: int, failing: int, last_success: datetime | None, expected: int | None) -> str:
    if failing > max(STRAGGLERS, STRAGGLER_SHARE * items):
        return "failing"
    if expected is None:
        return "unmonitored"
    if last_success is None:
        return "stale"
    age = datetime.now(UTC) - _utc(last_success)
    return "stale" if age.days >= expected else "ok"


def _tables(provider: str, dataset: str) -> list[str]:
    try:
        declared = registry.get(provider, dataset)
    except KeyError:
        return []
    names = {t.__tablename__ for target in declared.targets for t in rebuild.TABLES[target]}
    return sorted(names)


def feeds(session: Session) -> list[Feed]:
    """Every feed that has synced or fetched anything, plus monitored ones that never did."""
    failing_item = (SyncState.last_error.is_not(None)) & (
        SyncState.last_success.is_(None) | (SyncState.last_attempt > SyncState.last_success)
    )
    state = {
        (p, d): (n, f, ok)
        for p, d, n, f, ok in session.execute(
            select(
                SyncState.provider,
                SyncState.dataset,
                func.count(),
                func.sum(case((failing_item, 1), else_=0)),
                func.max(SyncState.last_success),
            ).group_by(SyncState.provider, SyncState.dataset)
        )
    }
    errors = dict(
        session.execute(
            select(
                func.concat(SyncState.provider, "/", SyncState.dataset),
                func.max(SyncState.last_error),
            )
            .where(failing_item)
            .group_by(SyncState.provider, SyncState.dataset)
        ).all()
    )
    raw = {
        (p, d): (n, last)
        for p, d, n, last in session.execute(
            select(
                RawResponse.provider,
                RawResponse.dataset,
                func.count(),
                func.max(RawResponse.fetched_at),
            ).group_by(RawResponse.provider, RawResponse.dataset)
        )
    }
    out = []
    for provider, dataset in sorted(set(state) | set(raw) | set(EXPECTED)):
        items, failing, last_success = state.get((provider, dataset), (0, 0, None))
        responses, last_fetch = raw.get((provider, dataset), (0, None))
        expected = EXPECTED.get((provider, dataset))
        out.append(
            Feed(
                provider,
                dataset,
                items,
                int(failing or 0),
                last_success,
                errors.get(f"{provider}/{dataset}"),
                expected,
                _status(items, int(failing or 0), last_success, expected),
                _tables(provider, dataset),
                responses,
                last_fetch,
                TERMS.get(provider),
            )
        )
    return out


def alerts(session: Session) -> list[Feed]:
    return [f for f in feeds(session) if f.status in ("stale", "failing")]


def table_rows(session: Session) -> dict[str, int]:
    """Estimated rows per table (PostgreSQL statistics: instant, close after ANALYZE)."""
    rows = session.execute(
        text(
            "SELECT relname, GREATEST(reltuples, 0)::bigint FROM pg_class "
            "WHERE relkind = 'r' AND relnamespace = 'public'::regnamespace"
        )
    )
    return {name: n for name, n in rows}


def as_dict(feed: Feed) -> dict[str, Any]:
    age = feed.age_days
    return {
        "provider": feed.provider,
        "dataset": feed.dataset,
        "status": feed.status,
        "items": feed.items,
        "failing": feed.failing,
        "last_success": feed.last_success,
        "age_days": round(age, 1) if age is not None else None,
        "expected_days": feed.expected_days,
        "last_error": feed.last_error,
        "tables": feed.tables,
        "raw_responses": feed.raw_responses,
        "last_fetch": feed.last_fetch,
        "terms": feed.terms,
    }
