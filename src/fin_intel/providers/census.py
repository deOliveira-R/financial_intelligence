from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

CONCORDANCES = {
    "sic1987-naics2002": "/naics/concordances/1987_SIC_to_2002_NAICS.xls",
    "naics2002-naics2007": "/naics/concordances/2002_to_2007_NAICS.xls",
    "naics2007-naics2012": "/naics/concordances/2007_to_2012_NAICS.xls",
    "naics2012-naics2017": "/naics/concordances/2012_to_2017_NAICS.xlsx",
}


class CensusProvider(Provider):
    """The Census Bureau's website (no key): industry classification concordances."""

    name = "census"
    base_url = "https://www.census.gov"
    limits = (Limit(1, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 (compatible; fin-intel research)"}

    def fetch_concordance(self, name: str) -> bytes:
        """One step between classifications (`sic1987-naics2002`...): SEC still assigns 1987
        SIC codes; BEA's tables use 2017 NAICS."""
        return self.get_bytes(CONCORDANCES[name], dataset="concordance", key=name)
