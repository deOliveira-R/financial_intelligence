from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import HOUR, SECOND, Limit


class EiaProvider(Provider):
    """US Energy Information Administration API v2. Works with the shared DEMO_KEY (a few
    dozen requests an hour); a free key (FI_EIA_API_KEY) lifts that."""

    name = "eia"
    base_url = "https://api.eia.gov/v2"
    limits = (Limit(1, SECOND), Limit(25, HOUR))

    def auth_params(self) -> dict[str, str]:
        return {"api_key": get_settings().eia_api_key}

    def fetch_series(self, route: str, series_id: str, frequency: str = "W") -> Any:
        """A series' full history by its classic ID (e.g. PET.WCESTUS1.W)."""
        return self.get(
            f"/seriesid/{route}.{series_id}.{frequency}", dataset="series", key=series_id
        )
