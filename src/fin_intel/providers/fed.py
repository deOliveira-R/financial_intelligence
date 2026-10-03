from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit


class FedProvider(Provider):
    """Federal Reserve Board website (no API): the FOMC meeting calendar."""

    name = "fed"
    base_url = "https://www.federalreserve.gov"
    limits = (Limit(1, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 (compatible; fin-intel research)"}

    def fetch_fomc_calendar(self) -> bytes:
        return self.get_bytes(
            "/monetarypolicy/fomccalendars.htm", dataset="fomc_calendar", key="all"
        )
