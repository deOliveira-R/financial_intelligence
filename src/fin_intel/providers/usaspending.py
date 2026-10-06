from datetime import date, timedelta
from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

CONTRACTS = ["A", "B", "C", "D"]  # definitive contracts, purchase and delivery orders, BPA calls


class UsaspendingProvider(Provider):
    """USAspending.gov API (no key): federal awards since fiscal 2008 (October 2007)."""

    name = "usaspending"
    base_url = "https://api.usaspending.gov/api/v2"
    # No published limit, but it drops connections when pushed (2 requests/s for a few
    # minutes did it): go slowly and back off for minutes.
    limits = (Limit(1, 2 * SECOND),)
    max_retries = 4
    retry_base = 30.0
    page_size = 100

    def fetch_naics_month(self, month: date, page: int) -> Any:
        """One page of a month's contract obligations by NAICS code, largest first."""
        end = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        body = {
            "filters": {
                "time_period": [{"start_date": month.isoformat(), "end_date": end.isoformat()}],
                "award_type_codes": CONTRACTS,
            },
            "limit": self.page_size,
            "page": page,
        }
        return self.post_json(
            "/search/spending_by_category/naics/",
            body,
            dataset="naics_month",
            key=f"{month:%Y-%m}|{page}",
        )
