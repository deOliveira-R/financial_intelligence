from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

PAGES = {"current": "/en/mopo/mpmsche_minu/index.htm", "past": "/en/mopo/mpmsche_minu/past.htm"}


class BojProvider(Provider):
    """Bank of Japan website (no API): the monetary policy meeting schedule."""

    name = "boj"
    base_url = "https://www.boj.or.jp"
    limits = (Limit(1, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 (compatible; fin-intel research)"}

    def fetch_mpm_schedule(self, page: str) -> bytes:
        """ "current": this year's and next year's meetings; "past": 2010 to last year."""
        return self.get_bytes(PAGES[page], dataset="mpm_schedule", key=page)
