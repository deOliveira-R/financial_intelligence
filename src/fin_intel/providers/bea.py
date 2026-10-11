from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

SUPPLY_USE = "/industry/iTables%20Static%20Files/AllTablesSUP.zip"


class BeaProvider(Provider):
    """The Bureau of Economic Analysis's static files (no key): the input-output accounts.
    BEA's keyed API serves the same tables one request per year and level; the zip has
    them all."""

    name = "bea"
    base_url = "https://apps.bea.gov"
    limits = (Limit(1, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 (compatible; fin-intel research)"}

    def fetch_supply_use(self) -> bytes:
        """Supply and use tables, supply-use framework (~20 MB): detail level (~400
        industries) for 2007, 2012 and 2017, summary level (71) for every year since 1997,
        and the concordance from BEA codes to NAICS. Revised yearly, each September."""
        return self.get_bytes(SUPPLY_USE, dataset="supply_use", key="AllTablesSUP")
