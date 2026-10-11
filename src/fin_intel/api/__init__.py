"""Read-only HTTP API over the stored data (FastAPI). Every route except /health needs
the X-API-Key header when FI_API_KEY is set."""

from fastapi import Depends

from fin_intel.api import fundamentals, macro, market, players, portfolio, research, system
from fin_intel.api.app import app, require_api_key

for _module in (system, market, fundamentals, players, macro, research, portfolio):
    app.include_router(_module.router, dependencies=[Depends(require_api_key)])

__all__ = ["app"]
