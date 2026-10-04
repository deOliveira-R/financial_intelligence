from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

# Industry formats: general, banks, securities firms, financial holdings, insurance, other.
INDUSTRIES = ("ci", "basi", "bd", "fh", "ins", "mim")
MARKETS = {
    # market -> (base url, endpoint prefix, endpoint suffix)
    "twse": ("https://openapi.twse.com.tw/v1/opendata", "", "L"),
    "tpex": ("https://www.tpex.org.tw/openapi/v1", "mopsfin_", "O"),
}


class TwseProvider(Provider):
    """Taiwan's exchanges' open data (TWSE listed, TPEx OTC): company profiles and the
    latest quarter's income statement and balance sheet for every company. No key."""

    name = "twse"
    base_url = ""
    limits = (Limit(2, SECOND),)

    def fetch(self, market: str, table: str, snapshot: str) -> Any:
        """One table: t187ap03 (profiles), t187ap06_<industry> (income statements),
        t187ap07_<industry> (balance sheets). Keyed by snapshot day: each day's table is
        kept, so quarters accumulate into a history."""
        base, prefix, suffix = MARKETS[market]
        name = (
            f"{prefix}t187ap03_{suffix}"
            if table == "t187ap03"
            else (f"{prefix}{table.replace('_', f'_{suffix}_', 1)}")
        )
        return self.get(f"{base}/{name}", dataset="table", key=f"{market}|{table}|{snapshot}")
