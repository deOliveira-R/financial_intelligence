from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

FIELDS = [
    "document_number",
    "executive_order_number",
    "proclamation_number",
    "title",
    "president",
    "signing_date",
    "publication_date",
    "disposition_notes",
    "raw_text_url",
]


class FederalRegisterProvider(Provider):
    """Federal Register API (no key): presidential documents since 1994."""

    name = "federalregister"
    base_url = "https://www.federalregister.gov/api/v1"
    # It answers 429 when pushed (2 requests/s for a minute did it): go slowly, back off long.
    limits = (Limit(1, 2 * SECOND),)
    max_retries = 4
    retry_base = 60.0

    def fetch_year(self, kind: str, year: int) -> Any:
        """A year's documents of one kind (executive_order, proclamation...), by publication."""
        params: dict[str, Any] = {
            "conditions[type][]": "PRESDOCU",
            "conditions[presidential_document_type]": kind,
            "conditions[publication_date][year]": year,
            "per_page": 1000,
            "order": "oldest",
            "fields[]": FIELDS,
        }
        return self.get(
            "/documents.json", params, dataset="presidential_list", key=f"{kind}|{year}"
        )

    def fetch_text(self, document_number: str, url: str) -> bytes:
        return self.get_bytes(url, dataset="presidential_text", key=document_number)
