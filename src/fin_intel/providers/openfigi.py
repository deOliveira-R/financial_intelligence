from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import MINUTE, SECOND, Limit


class OpenFigiProvider(Provider):
    """OpenFIGI: maps identifiers (here CUSIPs from 13F filings) to FIGIs. Free; an API key
    (FI_OPENFIGI_API_KEY) raises the limits from 25 requests/minute with 10 jobs each to 25
    requests per 6 seconds with 100 jobs each."""

    name = "openfigi"
    base_url = "https://api.openfigi.com/v3"

    def __init__(self, *args, **kwargs):
        keyed = bool(get_settings().openfigi_api_key)
        self.batch = 100 if keyed else 10
        type(self).limits = (Limit(24, 6 * SECOND),) if keyed else (Limit(24, MINUTE),)
        super().__init__(*args, **kwargs)

    def headers(self) -> dict[str, str]:
        key = get_settings().openfigi_api_key
        return {"X-OPENFIGI-APIKEY": key} if key else {}

    def map_cusips(self, cusips: list[str]) -> list[dict[str, Any]]:
        """One result per CUSIP, in order: {"data": [...]} or {"warning": ...}."""
        jobs = [{"idType": "ID_CUSIP", "idValue": c} for c in cusips]
        return self.post_json("/mapping", jobs, dataset="mapping", key=cusips[0])
