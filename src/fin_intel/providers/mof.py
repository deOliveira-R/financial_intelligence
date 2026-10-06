from datetime import date

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

JGB_HISTORY = "/english/policy/jgbs/reference/interest_rate/historical/jgbcme_all.csv"
JGB_CURRENT = "/english/policy/jgbs/reference/interest_rate/jgbcme.csv"
FLOWS = "/policy/international_policy/reference/itn_transactions_in_securities/week.csv"


class MofProvider(Provider):
    """Japan's Ministry of Finance website (CSV files, no API): the JGB yield curve and
    weekly portfolio flows."""

    name = "mof"
    base_url = "https://www.mof.go.jp"
    limits = (Limit(1, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 (compatible; fin-intel research)"}

    def fetch_jgb_history(self) -> bytes:
        """Every day since 1974 up to the end of last month (updated monthly)."""
        return self.get_bytes(JGB_HISTORY, dataset="jgb_curve", key="history")

    def fetch_jgb_current(self, today: date) -> bytes:
        """This month's days so far. Keyed by month so each month's file stays in raw until
        the history file covers it."""
        return self.get_bytes(JGB_CURRENT, dataset="jgb_curve", key=f"month:{today:%Y-%m}")

    def fetch_flows(self) -> bytes:
        """International transactions in securities, weekly since 2005 (full history)."""
        return self.get_bytes(FLOWS, dataset="flows", key="weekly")
