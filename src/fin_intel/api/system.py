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
