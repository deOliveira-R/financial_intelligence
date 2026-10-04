from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import MINUTE, Limit


class GleifProvider(Provider):
    """GLEIF, the global LEI registry: which securities (ISINs) an LEI has issued. No key."""

    name = "gleif"
    base_url = "https://api.gleif.org/api/v1"
    limits = (Limit(50, MINUTE),)

    def fetch_isins(self, lei: str) -> Any:
        """The first 200 ISINs (equity is usually among them; banks also list many bonds)."""
        params = {"page[size]": "200", "page[number]": "1"}
        return self.get(f"/lei-records/{lei}/isins", params, dataset="isins", key=lei)
