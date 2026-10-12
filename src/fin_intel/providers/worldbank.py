import re

from fin_intel.providers.base import Provider
from fin_intel.providers.errors import ProviderError
from fin_intel.providers.ratelimit import MINUTE, Limit

PAGE = "https://www.worldbank.org/en/research/commodity-markets"
FILE = re.compile(r'https://thedocs\.worldbank\.org/[^"\s]*CMO-Historical-Data-Monthly\.xlsx')


class WorldBankProvider(Provider):
    """The World Bank's commodity prices ("Pink Sheet"): monthly average prices of about 70
    commodities since 1960, updated early each month. CC BY 4.0. The workbook's address
    changes with each update, so it is read from the commodity markets page first."""

    name = "worldbank"
    base_url = "https://www.worldbank.org"
    limits = (Limit(5, MINUTE),)

    def monthly_url(self) -> str:
        # The page only points to the file; it isn't data, so it skips the raw store.
        resp = self.client.get(PAGE)
        resp.raise_for_status()
        found = FILE.search(resp.text)
        if not found:
            raise ProviderError("worldbank: no monthly workbook link on the commodity page")
        return found.group(0)

    def fetch_monthly(self, key: str) -> bytes:
        return self.get_bytes(self.monthly_url(), dataset="pinksheet", key=key)
