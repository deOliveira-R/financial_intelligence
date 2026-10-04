import csv
import io
import zipfile
from datetime import date

from fin_intel.providers.base import Provider
from fin_intel.providers.ratelimit import MINUTE, Limit

MAPPING_URL = "https://mapping.gleif.org/api/v2/isin-lei/latest/download"


class GleifProvider(Provider):
    """GLEIF, the global LEI registry: which securities (ISINs) each LEI has issued, from
    the daily ISIN-LEI mapping file (~32 MB zipped). No key."""

    name = "gleif"
    base_url = "https://api.gleif.org/api/v1"
    limits = (Limit(10, MINUTE),)

    def fetch_mapping(self) -> bytes:
        return self.get_bytes(MAPPING_URL, dataset="isin_lei", key=date.today().isoformat())


def isins_by_lei(data: bytes, leis: set[str]) -> dict[str, list[str]]:
    """The ISINs of the given LEIs, from the mapping zip."""
    out: dict[str, list[str]] = {}
    with zipfile.ZipFile(io.BytesIO(data)) as archive, archive.open(archive.namelist()[0]) as f:
        for row in csv.reader(io.TextIOWrapper(f, encoding="utf-8")):
            if len(row) >= 2 and row[0] in leis:
                out.setdefault(row[0], []).append(row[1])
    return out
