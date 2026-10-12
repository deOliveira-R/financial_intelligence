from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import MINUTE, Limit


class UcdpProvider(Provider):
    """Uppsala Conflict Data Program downloads (CC BY 4.0, no account): the yearly
    Georeferenced Event Dataset and the monthly candidate releases. Its API needs a token;
    the downloads don't."""

    name = "ucdp"
    base_url = "https://ucdp.uu.se/downloads"
    limits = (Limit(10, MINUTE),)

    def fetch_ged(self, version: str) -> bytes:
        """The yearly dataset as a zipped CSV (version "261" = 26.1, through the prior year)."""
        return self.get_bytes(f"/ged/ged{version}-csv.zip", dataset="ged", key=version)

    def fetch_candidate(self, version: str) -> bytes:
        """A monthly candidate release as CSV (version "26_0_8" = August 2026)."""
        return self.get_bytes(
            f"/candidateged/GEDEvent_v{version}.csv", dataset="candidate", key=version
        )
