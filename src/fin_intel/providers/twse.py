from datetime import date
from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

DAILY_TWSE = "https://www.twse.com.tw/exchangeReport/MI_INDEX"
DAILY_TPEX = (
    "https://www.tpex.org.tw/web/stock/aftertrading/daily_close_quotes/stk_quote_result.php"
)

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
    limits = (Limit(1, 3 * SECOND),)  # the exchanges' sites block faster clients

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

    def fetch_prices(self, market: str, day: date) -> Any:
        """Every stock's daily quote on one trading day (an empty table on holidays)."""
        if market == "twse":
            params = {"response": "json", "date": day.strftime("%Y%m%d"), "type": "ALLBUT0999"}
            url = DAILY_TWSE
        else:
            roc = f"{day.year - 1911}/{day:%m/%d}"
            params, url = {"l": "zh-tw", "d": roc, "o": "json"}, DAILY_TPEX
        return self.get(url, params, dataset="prices", key=f"{market}|{day.isoformat()}")
