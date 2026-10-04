from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit


class EsefProvider(Provider):
    """filings.xbrl.org: European (ESEF) annual reports as xBRL-JSON, keyed by LEI. No key."""

    name = "esef"
    base_url = "https://filings.xbrl.org"
    limits = (Limit(2, SECOND),)
    page_size = 500

    def fetch_index(self, page: int) -> Any:
        """One page of the filing index (oldest first, so pages fill up at the end), with
        each filing's entity (LEI and name)."""
        params = {
            "page[size]": str(self.page_size),
            "page[number]": str(page),
            "sort": "date_added",
            "include": "entity",
        }
        return self.get("/api/filings", params, dataset="index", key=str(page))

    def fetch_report(self, json_url: str, key: str) -> bytes:
        return self.get_bytes(json_url, dataset="report", key=key)
