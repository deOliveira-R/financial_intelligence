from datetime import date
from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

SHORT_INTEREST = "/data/group/otcMarket/name/consolidatedShortInterest"


def _on(settlement: date) -> dict[str, Any]:
    return {
        "compareFilters": [
            {"compareType": "EQUAL", "fieldName": "settlementDate", "fieldValue": str(settlement)}
        ]
    }


class FinraProvider(Provider):
    """FINRA Query API, public datasets (no credentials): consolidated short interest for
    exchange-listed and OTC stocks, twice a month since December 2017."""

    name = "finra"
    base_url = "https://api.finra.org"
    limits = (Limit(2, SECOND),)
    page_size = 5000  # the API's maximum

    def headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    def records_on(self, settlement: date) -> int:
        """How many records a settlement date has (0: not a settlement date)."""
        resp = self._request(
            SHORT_INTEREST,
            None,
            dataset="short_interest_probe",
            key=str(settlement),
            json_body={"limit": 1, **_on(settlement)},
        )
        return int(resp.headers.get("record-total") or 0) if resp.status_code == 200 else 0

    def fetch_short_interest(self, settlement: date, offset: int) -> Any:
        return self.post_json(
            SHORT_INTEREST,
            {"limit": self.page_size, "offset": offset, **_on(settlement)},
            dataset="short_interest",
            key=f"{settlement}|{offset}",
        )
