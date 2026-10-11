"""Sync bookkeeping: per-item outcomes in sync_state, and dates as syncs see them."""

import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import Security, SyncState

log = logging.getLogger(__name__)


def utc_today() -> date:
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


def synced_since(session: Session, provider: str, dataset: str, since: datetime) -> set[str]:
    """Keys of a dataset last synced successfully at or after `since`."""
    rows = session.execute(
        select(SyncState.key, SyncState.last_success).where(
            SyncState.provider == provider,
            SyncState.dataset == dataset,
            SyncState.last_success.is_not(None),
        )
    )
    # SQLite returns naive datetimes; they're stored in UTC.
    return {k for k, ok in rows if (ok if ok.tzinfo else ok.replace(tzinfo=UTC)) >= since}


def tracked_ciks(session: Session) -> set[int]:
    """Issuers we follow: those with at least one security, active or delisted."""
    return set(session.scalars(select(Security.cik).where(Security.cik.is_not(None)).distinct()))
