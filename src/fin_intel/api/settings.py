"""Settings the dashboard can change: provider keys, the SEC identity, the watchlist
(settings_store.py). Values come back masked; a save retries the steps they blocked."""

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from fin_intel import settings_store
from fin_intel.api.app import SessionDep

router = APIRouter()


class SettingOut(BaseModel):
    name: str
    env: str  # the .env variable it overrides
    label: str
    group: str  # keys, identity, watchlist
    help: str
    url: str | None
    secret: bool
    required: bool
    source: str | None  # dashboard, env, default, or None (not set)
    value: str | None  # masked when secret
    updated_at: datetime | None
    status: str  # working, rejected, missing, not set, unused, default
    detail: str | None
    last_used: datetime | None


class SettingIn(BaseModel):
    value: str


class SavedOut(BaseModel):
    retried: list[str]  # steps retried now


@router.get("/settings", response_model=list[SettingOut])
def list_settings(session: SessionDep) -> list[SettingOut]:
    return [SettingOut(**s) for s in settings_store.overview(session)]


@router.put("/settings/{name}", response_model=SavedOut)
def save_setting(session: SessionDep, name: str, body: SettingIn) -> SavedOut:
    """Save a value over .env's; it applies to the next step that runs."""
    if name not in settings_store.EDITABLE:
        raise HTTPException(404, f"unknown setting {name}")
    if not body.value.strip():
        raise HTTPException(400, "empty value: delete the setting to use .env's")
    return SavedOut(retried=settings_store.save(session, name, body.value))


@router.delete("/settings/{name}", status_code=204)
def delete_setting(session: SessionDep, name: str) -> None:
    """Forget the saved value: .env's applies again."""
    if name not in settings_store.EDITABLE:
        raise HTTPException(404, f"unknown setting {name}")
    settings_store.forget(session, name)
