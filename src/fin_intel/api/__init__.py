"""HTTP API over the stored data (FastAPI), and the dashboard at `/` (frontend/, built
into frontend/dist). Every data route needs the X-API-Key header or a tailnet user when
FI_API_KEY is set; the only writes are the dashboard's settings."""

from pathlib import Path

from fastapi import Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fin_intel.api import (
    fundamentals,
    macro,
    market,
    players,
    portfolio,
    research,
    settings,
    system,
)
from fin_intel.api.app import app, require_api_key

for _module in (system, settings, market, fundamentals, players, macro, research, portfolio):
    app.include_router(_module.router, dependencies=[Depends(require_api_key)])

# The built dashboard: static files, no data (that comes from the routes above).
UI_DIR = Path(__file__).resolve().parents[3] / "frontend" / "dist"
app.mount("/assets", StaticFiles(directory=UI_DIR / "assets", check_dir=False), name="assets")


@app.get("/", include_in_schema=False)
def dashboard():
    index = UI_DIR / "index.html"
    if not index.exists():
        return JSONResponse({"detail": "dashboard not built (cd frontend && npm run build)"}, 404)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


__all__ = ["app"]
