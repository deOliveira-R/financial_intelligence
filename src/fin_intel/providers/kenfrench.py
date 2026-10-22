from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import SECOND, Limit

FTP = "/pages/faculty/ken.french/ftp/{name}"


class KenFrenchProvider(Provider):
    """Kenneth French's data library (Dartmouth): factor and portfolio returns since 1926,
    one zipped CSV per file, refreshed monthly. Free, no account."""

    name = "kenfrench"
    base_url = "https://mba.tuck.dartmouth.edu"
    limits = (Limit(1, SECOND),)

    def headers(self) -> dict[str, str]:
        return {"User-Agent": "Mozilla/5.0 (compatible; fin-intel research)"}

    def fetch_file(self, name: str) -> bytes:
        """A library file by its name, e.g. `F-F_Research_Data_Factors_daily_CSV.zip`."""
        return self.get_bytes(FTP.format(name=name), dataset="file", key=name)
