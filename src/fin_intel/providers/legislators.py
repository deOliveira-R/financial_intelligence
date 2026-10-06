from typing import Any

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

FILES = {
    "current": "legislators-current.json",
    "historical": "legislators-historical.json",
    "committees": "committees-current.json",
    "membership": "committee-membership-current.json",
}


class LegislatorsProvider(Provider):
    """unitedstates/congress-legislators (public domain): every member of Congress with
    their terms, and current committees and assignments."""

    name = "legislators"
    base_url = "https://unitedstates.github.io/congress-legislators"
    limits = (Limit(1, SECOND),)

    def fetch(self, file: str) -> Any:
        return self.get(f"/{FILES[file]}", dataset="file", key=file)
