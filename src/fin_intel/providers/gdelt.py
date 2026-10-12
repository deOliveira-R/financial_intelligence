import json
from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.errors import ProviderError
from fin_intel.providers.ratelimit import Limit

# GDELT answers some errors with HTTP 200 and a sentence instead of JSON.
TEXT_ERRORS = ("Please limit requests", "Your search", "The specified", "Invalid query")


class GdeltProvider(Provider):
    """GDELT DOC 2.0 API: worldwide online news in 65 languages (machine-translated for
    search), as daily article counts matching a query since 2017. Free, no account; one
    request every 5 seconds, enforced with HTTP 429, so requests are spaced 10 seconds."""

    name = "gdelt"
    base_url = "https://api.gdeltproject.org/api/v2"
    limits = (Limit(1, 10.0),)
    retry_base = 20.0

    def fetch_timeline(self, key: str, query: str, start: str, end: str) -> Any:
        """Daily counts of articles matching `query` between two YYYYMMDDHHMMSS stamps, with
        each day's total of monitored articles (`norm`)."""
        params = {
            "query": query,
            "mode": "timelinevolraw",
            "format": "json",
            "startdatetime": start,
            "enddatetime": end,
        }
        text = self.get_bytes("/doc/doc", params, dataset="doc_timeline", key=key).decode()
        if text.lstrip().startswith(TEXT_ERRORS):
            raise ProviderError(f"gdelt: {text[:200]}")
        return json.loads(text)
