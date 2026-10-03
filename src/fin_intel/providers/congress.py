"""Congressional financial disclosures: House Clerk and Senate eFD (no keys)."""

import re
from typing import Any

from fin_intel.providers.base import Provider, ProviderError
from fin_intel.providers.ratelimit import SECOND, Limit

# Both sites serve browsers; a plain client User-Agent is sometimes refused.
USER_AGENT = "Mozilla/5.0 (compatible; fin-intel research)"


class HouseProvider(Provider):
    """House Clerk: a yearly index of disclosure filings and each report as a PDF."""

    name = "house"
    base_url = "https://disclosures-clerk.house.gov/public_disc"
    limits = (Limit(2, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT}

    def fetch_index(self, year: int) -> bytes:
        """Zip with {year}FD.xml: every disclosure filed that year (PTRs are type P)."""
        return self.get_bytes(f"/financial-pdfs/{year}FD.zip", dataset="fd_index", key=str(year))

    def fetch_ptr(self, doc_id: str, year: int) -> bytes:
        return self.get_bytes(f"/ptr-pdfs/{year}/{doc_id}.pdf", dataset="ptr", key=doc_id)


class SenateProvider(Provider):
    """Senate eFD: search requires accepting the site's terms (a session cookie), then
    returns reports as JSON; electronic reports are HTML pages."""

    name = "senate"
    base_url = "https://efdsearch.senate.gov"
    limits = (Limit(1, SECOND),)
    page_size = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._agreed = False

    def headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT, "Referer": self.base_url + "/search/"}

    def _agree(self) -> None:
        """Accept the terms of use (not recorded: it's session setup, not data)."""
        home = self.base_url + "/search/home/"
        page = self.client.get(home, headers=self.headers())
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.text)
        if not token:
            raise ProviderError("senate: terms page without a CSRF token")
        self.client.post(
            home,
            data={"prohibition_agreement": "1", "csrfmiddlewaretoken": token.group(1)},
            headers=self.headers() | {"Referer": home},
        )
        self._agreed = True

    def _csrf(self) -> str:
        if not self._agreed:
            self._agree()
        return self.client.cookies.get("csrftoken") or ""

    def search_ptrs(self, since: str, start: int = 0) -> dict[str, Any]:
        """One page of PTRs (report type 11) submitted on or after `since` (MM/DD/YYYY)."""
        form = {
            "start": str(start),
            "length": str(self.page_size),
            "report_types": "[11]",
            "filer_types": "[]",
            "submitted_start_date": f"{since} 00:00:00",
            "submitted_end_date": "",
            "candidate_state": "",
            "senator_state": "",
            "office_id": "",
            "first_name": "",
            "last_name": "",
        }
        return self.post_form(
            "/search/report/data/",
            form,
            dataset="search",
            key=f"{since}:{start}",
            headers={"X-CSRFToken": self._csrf()},
        )

    def fetch_ptr(self, doc_id: str) -> bytes:
        """An electronic PTR's page; re-accepts the terms once if the session lapsed (the
        site then redirects to its home page)."""
        status = None
        for _ in range(2):
            self._csrf()
            resp = self._request(f"/search/view/ptr/{doc_id}/", None, dataset="ptr", key=doc_id)
            if resp.status_code == 200 and b"<tbody" in resp.content:
                return resp.content
            status = resp.status_code
            self._agreed = False
        raise ProviderError(f"senate: report {doc_id} unavailable (status {status})")
