from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import MINUTE, Limit

JST_URL = "https://www.macrohistory.net/app/download/9834512569/JSTdatasetR6.xlsx"


class MacrohistoryProvider(Provider):
    """The Jordà-Schularick-Taylor Macrohistory Database (macrohistory.net): 18 advanced
    economies, yearly since 1870. Free under CC BY-NC-SA 4.0: personal research yes,
    commercial redistribution no."""

    name = "macrohistory"
    base_url = "https://www.macrohistory.net"
    limits = (Limit(5, MINUTE),)

    def fetch_jst(self, release: str = "R6") -> bytes:
        return self.get_bytes(JST_URL, dataset="jst", key=release)
