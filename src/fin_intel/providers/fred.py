from datetime import date, datetime
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider
from fin_intel.providers.ratelimit import MINUTE, Limit


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

    def fetch_observations(self, series_id: str) -> Any:
        return self.get(
            "/series/observations",
            {"series_id": series_id},
            dataset="observations",
            key=series_id,
        )


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
