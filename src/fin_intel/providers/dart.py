from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider, ProviderError
from fin_intel.providers.errors import QuotaExceededError
from fin_intel.providers.ratelimit import DAY, SECOND, Limit

NO_DATA = "013"
QUOTA = ("020", "021")


class DartProvider(Provider):
    """Korea's DART (OpenDART API, free key FI_OPENDART_API_KEY): listed companies and their
    financial statements. About 20,000 requests a day per key."""

    name = "dart"
    base_url = "https://opendart.fss.or.kr/api"
    limits = (Limit(5, SECOND), Limit(19_500, DAY))

    def auth_params(self) -> dict[str, str]:
        key = get_settings().opendart_api_key
        if not key:
            raise NotConfiguredError("set FI_OPENDART_API_KEY")
        return {"crtfc_key": key}

    def _json(self, path: str, params: dict[str, str], dataset: str, key: str) -> Any:
        """The payload, or None when DART has no data for the request (status 013)."""
        payload = self.get(path, params, dataset=dataset, key=key)
        status = payload.get("status")
        if status == NO_DATA:
            return None
        if status in QUOTA:
            raise QuotaExceededError(f"dart: {payload.get('message')}")
        if status != "000":
            raise ProviderError(f"dart: status {status}: {payload.get('message')}")
        return payload

    def fetch_corp_codes(self) -> bytes:
        """Zip of CORPCODE.xml: every entity's corp code, name and (if listed) stock code."""
        return self.get_bytes("/corpCode.xml", dataset="corp_codes", key="all")

    def fetch_company(self, corp_code: str) -> Any:
        return self._json("/company.json", {"corp_code": corp_code}, "company", corp_code)

    def fetch_statements(self, corp_code: str, year: int, report: str, fs_div: str) -> Any:
        """All accounts of one report: fs_div CFS (consolidated) or OFS (separate)."""
        params = {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report,
            "fs_div": fs_div,
        }
        key = f"{corp_code}|{year}|{report}|{fs_div}"
        return self._json("/fnlttSinglAcntAll.json", params, "statements", key)

    def fetch_share_counts(self, corp_code: str, year: int, report: str) -> Any:
        params = {"corp_code": corp_code, "bsns_year": str(year), "reprt_code": report}
        key = f"{corp_code}|{year}|{report}"
        return self._json("/stockTotqySttus.json", params, "share_counts", key)
