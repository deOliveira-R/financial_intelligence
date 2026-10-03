from datetime import date
from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit


class CftcProvider(Provider):
    """CFTC Public Reporting (Socrata): Commitments of Traders reports. No key needed."""

    name = "cftc"
    base_url = "https://publicreporting.cftc.gov/resource"
    limits = (Limit(2, SECOND),)
    page_size = 50_000

    def fetch_reports(
        self, report: str, dataset_id: str, codes: list[str], since: date, offset: int = 0
    ) -> list[dict[str, Any]]:
        """One page of a report's rows for the given markets, on or after `since`."""
        markets = ",".join(f"'{c}'" for c in codes)
        params = {
            "$where": f"cftc_contract_market_code in ({markets}) "
            f"AND report_date_as_yyyy_mm_dd >= '{since.isoformat()}'",
            "$order": "report_date_as_yyyy_mm_dd, cftc_contract_market_code",
            "$limit": str(self.page_size),
            "$offset": str(offset),
        }
        return self.get(
            f"/{dataset_id}.json", params, dataset=report, key=f"{since.isoformat()}:{offset}"
        )
