from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import MINUTE, Limit


class BisProvider(Provider):
    """Bank for International Settlements statistics (SDMX API, free, no account): whole
    datasets as CSV, e.g. total credit by borrowing sector for ~45 economies since the
    1940s-1990s, debt service ratios, credit-to-GDP gaps."""

    name = "bis"
    base_url = "https://stats.bis.org/api/v2"
    limits = (Limit(20, MINUTE),)

    def fetch_dataset(self, flow: str, version: str) -> bytes:
        return self.get_bytes(
            f"/data/dataflow/BIS/{flow}/{version}/~/",
            {"format": "csv"},
            dataset="dataset",
            key=f"{flow}|{version}",
        )
