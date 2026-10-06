from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

BILL_TYPES = ("hr", "s", "hjres", "sjres")  # bills and joint resolutions (can become law)


class GovinfoProvider(Provider):
    """GovInfo bulk data (GPO, no key): Bill Status XML, one zip per congress and type."""

    name = "govinfo"
    base_url = "https://www.govinfo.gov/bulkdata"
    limits = (Limit(1, SECOND),)

    def fetch_billstatus(self, congress: int, bill_type: str) -> bytes:
        return self.get_bytes(
            f"/BILLSTATUS/{congress}/{bill_type}/BILLSTATUS-{congress}-{bill_type}.zip",
            dataset="billstatus",
            key=f"{congress}-{bill_type}",
        )
