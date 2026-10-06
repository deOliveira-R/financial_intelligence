import json
from datetime import date
from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.errors import ProviderError
from fin_intel.providers.ratelimit import SECOND, Limit

ARCHIVE = "/resources/options/volume_and_call_put_ratios/{product}pc.csv"
DAILY = "/data/us/options/market_statistics/daily/{day}_daily_options"


class CboeProvider(Provider):
    """Cboe's public CDN: options volume and put/call ratios (archive files through October
    2019, one JSON file per trading day since)."""

    name = "cboe"
    base_url = "https://cdn.cboe.com"
    limits = (Limit(2, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 (compatible; fin-intel research)"}

    def fetch_archive(self, product: str) -> bytes:
        """`total`, `equity` or `index`: daily volumes from 2006 to 2019-10-04."""
        return self.get_bytes(ARCHIVE.format(product=product), dataset="pc_archive", key=product)

    def fetch_daily(self, day: date) -> Any | None:
        """A trading day's volumes and ratios; None for days without a file (holidays,
        weekends: the CDN answers 403 AccessDenied)."""
        resp = self._raw(DAILY.format(day=day.isoformat()), day)
        if resp is None:
            return None
        return json.loads(resp)

    def _raw(self, path: str, day: date) -> bytes | None:
        try:
            return self.get_bytes(path, dataset="daily_options", key=day.isoformat())
        except ProviderError as exc:
            if "403" in str(exc):
                return None
            raise
