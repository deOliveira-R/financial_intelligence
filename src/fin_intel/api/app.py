"""The API application: the key check, the database session dependency, and lookups
every router shares. Routers live in modules by area."""

import secrets
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from fin_intel import (
    symbols,
)
from fin_intel.config import get_settings
from fin_intel.db import get_session
from fin_intel.models import (
    Security,
)


def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    """With FI_API_KEY set, every endpoint but /health needs the header X-API-Key."""
    expected = get_settings().api_key
    if expected and not (x_api_key and secrets.compare_digest(x_api_key, expected)):
        raise HTTPException(401, "missing or invalid X-API-Key", {"WWW-Authenticate": "ApiKey"})


app = FastAPI(title="Financial Intelligence API", version="0.2.0")

SessionDep = Annotated[Session, Depends(get_session)]


class Orm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def security_or_404(session: Session, ticker: str) -> Security:
    security = symbols.get_security(session, ticker)
    if security is None:
        raise HTTPException(404, f"unknown ticker {ticker}")
    return security


def issuer_cik_or_404(session: Session, ticker: str) -> int:
    security = security_or_404(session, ticker)
    if security.cik is None:
        raise HTTPException(404, f"{ticker} has no SEC filings")
    return security.cik


@app.get("/health")  # unauthenticated: for uptime checks
def health() -> dict[str, str]:
    return {"status": "ok"}
