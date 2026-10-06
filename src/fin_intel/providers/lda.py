from datetime import date
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import MINUTE, Limit

PERIODS = {1: "first_quarter", 2: "second_quarter", 3: "third_quarter", 4: "fourth_quarter"}


class LdaProvider(Provider):
    """Senate Lobbying Disclosure Act database API (lda.gov). Free; with a key
    (FI_LDA_API_KEY) about 120 requests/minute, anonymous far fewer. 25 filings a page."""

    name = "lda"
    base_url = "https://lda.gov/api/v1"
    limits = (Limit(100, MINUTE),)

    def headers(self) -> dict[str, str]:
        key = get_settings().lda_api_key
        return {"Authorization": f"Token {key}"} if key else {}

    def fetch_quarter(self, year: int, quarter: int, page: int) -> Any:
        """One page of a quarter's filings (reports, amendments, terminations...)."""
        params = {"filing_year": year, "filing_period": PERIODS[quarter], "page": page}
        return self.get("/filings/", params, dataset="filings", key=f"{year}-Q{quarter}|{page}")

    def fetch_posted_since(self, since: date, page: int) -> Any:
        """One page of filings posted on or after `since` (incremental updates)."""
        params = {"filing_dt_posted_after": since.isoformat(), "page": page}
        return self.get("/filings/", params, dataset="filings", key=f"posted:{since}|{page}")
