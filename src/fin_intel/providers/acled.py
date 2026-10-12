from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import Provider
from fin_intel.providers.errors import NotConfiguredError, ProviderError
from fin_intel.providers.ratelimit import MINUTE, Limit

TOKEN_URL = "https://acleddata.com/oauth/token"
PAGE = 5000  # the API's largest page


class AcledProvider(Provider):
    """ACLED (Armed Conflict Location & Event Data): political violence and protest events
    worldwide. Needs the user's ACLED account (FI_ACLED_EMAIL, FI_ACLED_PASSWORD), which
    buys a bearer token valid 24 hours. Event data needs the Research tier or above:
    accounts on personal email domains get Open access, which logs in but is refused data
    (HTTP 403). The token request goes straight to the HTTP client,
    never through the raw store, so the password isn't recorded."""

    name = "acled"
    base_url = "https://acleddata.com/api"
    limits = (Limit(30, MINUTE),)

    _token: str | None = None

    def headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = self._login()
        return {"Authorization": f"Bearer {self._token}"}

    def _login(self) -> str:
        settings = get_settings()
        if not (settings.acled_email and settings.acled_password):
            raise NotConfiguredError("set FI_ACLED_EMAIL and FI_ACLED_PASSWORD")
        self.limiter.acquire()
        resp = self.client.post(
            TOKEN_URL,
            data={
                "username": settings.acled_email,
                "password": settings.acled_password,
                "grant_type": "password",
                "client_id": "acled",
                "scope": "authenticated",
            },
        )
        if resp.is_error:
            raise ProviderError(f"acled: login failed, HTTP {resp.status_code}")
        return resp.json()["access_token"]

    def fetch_month(self, month: str, first: str, last: str, page: int) -> Any:
        """One page of the events dated within a month (`month` is YYYY-MM)."""
        params = {
            "_format": "json",
            "event_date": f"{first}|{last}",
            "event_date_where": "BETWEEN",
            "limit": PAGE,
            "page": page,
        }
        return self.get("/acled/read", params, dataset="events", key=f"{month}|{page}")
