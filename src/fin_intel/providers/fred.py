from datetime import date, datetime
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider
from fin_intel.providers.ratelimit import MINUTE, Limit

MAX_ROWS = 100_000  # FRED's page size limit
MAX_VINTAGES = 1_900  # per request; FRED allows 2,000 vintage dates for JSON


class FredProvider(Provider):
    """St. Louis Fed FRED: US and international economic time series."""

    name = "fred"
    base_url = "https://api.stlouisfed.org/fred"
    limits = (Limit(100, MINUTE),)  # FRED allows ~120/min

    def auth_params(self) -> dict[str, str]:
        key = get_settings().fred_api_key
        if not key:
            raise NotConfiguredError("set FI_FRED_API_KEY")
        return {"api_key": key, "file_type": "json"}

    def fetch_series(self, series_id: str) -> Any:
        return self.get("/series", {"series_id": series_id}, dataset="series", key=series_id)

    def fetch_vintage_dates(self, series_id: str, since: date | None = None) -> list[date]:
        """Dates on which the series was published or revised (ALFRED), oldest first."""
        dates: list[date] = []
        while True:
            params: dict[str, Any] = {"series_id": series_id, "limit": 10000, "offset": len(dates)}
            if since:
                params["realtime_start"] = since.isoformat()
            payload = self.get(
                "/series/vintagedates", params, dataset="vintage_dates", key=series_id
            )
            batch = [date.fromisoformat(d) for d in payload["vintage_dates"]]
            dates += batch
            if not batch or len(dates) >= payload.get("count", len(dates)):
                return dates

    def fetch_vintages(self, series_id: str, start: date, end: date) -> list[Any]:
        """Every version of every observation valid between two vintage dates. A request
        may span at most 2,000 vintage dates and returns at most 100,000 rows per page."""
        pages, offset = [], 0
        while True:
            payload = self.get(
                "/series/observations",
                {
                    "series_id": series_id,
                    "realtime_start": start.isoformat(),
                    "realtime_end": end.isoformat(),
                    "limit": MAX_ROWS,
                    "offset": offset,
                },
                dataset="vintages",
                key=series_id,
            )
            pages.append(payload)
            offset += len(payload["observations"])
            if not payload["observations"] or offset >= payload.get("count", offset):
                return pages

    def fetch_observations(self, series_id: str) -> Any:
        return self.get(
            "/series/observations",
            {"series_id": series_id},
            dataset="observations",
            key=series_id,
        )


def parse_vintages(series_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "series_id": series_id,
            "date": date.fromisoformat(obs["date"]),
            "realtime_start": date.fromisoformat(obs["realtime_start"]),
            "value": None if obs["value"] == "." else float(obs["value"]),
        }
        for obs in payload["observations"]
    ]


def parse_series(payload: dict[str, Any]) -> dict[str, Any]:
    info = payload["seriess"][0]
    return {
        "id": info["id"],
        "source": FredProvider.name,
        "title": info.get("title"),
        "units": info.get("units_short") or info.get("units"),
        "frequency": info.get("frequency_short"),
        "seasonal_adjustment": info.get("seasonal_adjustment_short"),
        "last_updated": _parse_updated(info.get("last_updated")),
    }


def parse_observations(series_id: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "series_id": series_id,
            "date": date.fromisoformat(obs["date"]),
            # FRED marks missing values with "."
            "value": None if obs["value"] == "." else float(obs["value"]),
        }
        for obs in payload["observations"]
    ]


def _parse_updated(value: str | None) -> datetime | None:
    # e.g. "2026-09-12 07:51:02-05"
    if not value:
        return None
    return datetime.strptime(value + "00", "%Y-%m-%d %H:%M:%S%z")
