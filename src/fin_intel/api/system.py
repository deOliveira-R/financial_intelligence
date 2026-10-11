"""Health and sync status."""

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from fin_intel.api.app import Orm, SessionDep
from fin_intel.models import (
    SyncRun,
    SyncState,
)

router = APIRouter()


class SyncStateOut(Orm):
    provider: str
    dataset: str
    key: str
    last_attempt: datetime
    last_success: datetime | None
    last_error: str | None
    rows: int | None


class SyncRunOut(Orm):
    id: int
    job: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    items_ok: int
    items_failed: int
    message: str | None


class StatusOut(BaseModel):
    recent_runs: list[SyncRunOut]
    failing: list[SyncStateOut]


@router.get("/status", response_model=StatusOut)
def status(session: SessionDep) -> StatusOut:
    """Recent sync runs and every item whose last sync failed."""
    runs = session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(20))
    failing = session.scalars(
        select(SyncState).where(SyncState.last_error.is_not(None)).order_by(SyncState.key)
    )
    return StatusOut(
        recent_runs=[SyncRunOut.model_validate(r) for r in runs],
        failing=[SyncStateOut.model_validate(s) for s in failing],
    )


@router.get("/catalog")
def data_catalog(session: SessionDep, alerts_only: bool = False):
    """Every feed: status (ok, stale, failing, unmonitored), items and failing items, last
    success and its age against the expected freshness, the tables it loads, and raw
    responses kept; plus estimated rows per table."""
    from fin_intel import catalog

    feeds = [catalog.as_dict(f) for f in catalog.feeds(session)]
    if alerts_only:
        feeds = [f for f in feeds if f["status"] in ("stale", "failing")]
    return {"feeds": feeds, "tables": catalog.table_rows(session)}
