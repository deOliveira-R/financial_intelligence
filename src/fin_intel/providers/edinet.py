import io
import logging
import zipfile
from datetime import date
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider, ProviderError
from fin_intel.providers.errors import QuotaExceededError
from fin_intel.providers.ratelimit import SECOND, Limit

log = logging.getLogger(__name__)

# Document types read: annual securities report, quarterly report (until 2024), half-year
# report (semiannual since 2024).
DOC_TYPES = {"120": "annual", "140": "quarterly", "160": "half-year"}


class EdinetProvider(Provider):
    """Japan's EDINET (API v2, free key FI_EDINET_API_KEY): every listed company's filings."""

    name = "edinet"
    base_url = "https://api.edinet-fsa.go.jp/api/v2"
    limits = (Limit(2, SECOND),)

    def auth_params(self) -> dict[str, str]:
        key = get_settings().edinet_api_key
        if not key:
            raise NotConfiguredError("set FI_EDINET_API_KEY")
        return {"Subscription-Key": key}

    def fetch_documents(self, day: date) -> Any:
        """Every document filed on a day, with its type, filer and periods."""
        return self.get(
            "/documents.json",
            {"date": day.isoformat(), "type": "2"},
            dataset="documents",
            key=day.isoformat(),
        )

    def fetch_instance(self, doc_id: str, key: str) -> bytes | None:
        """A filing's XBRL instance. EDINET serves a zip of the whole report (HTML, images,
        linkbases: 0.5-2 MB); only the instance is kept as the raw record (~100 KB
        gzipped), which is all the facts need. None if the zip has no instance."""
        self.limiter.acquire()
        resp = self.client.get(
            f"{self.base_url}/documents/{doc_id}", params={"type": "1", **self.auth_params()}
        )
        if resp.status_code == 429:
            raise QuotaExceededError("edinet: rate limited")
        if resp.is_error or not resp.content.startswith(b"PK"):
            raise ProviderError(f"edinet: HTTP {resp.status_code} for {doc_id}: {resp.text[:200]}")
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            names = [
                n
                for n in archive.namelist()
                if n.startswith("XBRL/PublicDoc/") and n.endswith(".xbrl")
            ]
            if not names:
                return None
            instance = archive.read(names[0])
        if self.raw_store:
            self.raw_store.save(self.name, "instance", key, {"doc_id": doc_id}, 200, instance)
        return instance
