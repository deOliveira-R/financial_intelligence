import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider, ProviderError
from fin_intel.providers.errors import NotFoundError
from fin_intel.providers.ratelimit import SECOND, Limit

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
THIRTEENF_DATASETS_PAGE = "https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets"
INSIDER_DATASETS_PAGE = (
    "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
)
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
BULK_COMPANY_FACTS_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"


class SecProvider(Provider):
    """SEC EDGAR: ticker/CIK map and XBRL company facts. No key; requires a User-Agent."""

    name = "sec"
    base_url = "https://data.sec.gov"
    limits = (Limit(8, SECOND),)  # SEC allows 10/s; stay under it

    def headers(self) -> dict[str, str]:
        user_agent = get_settings().sec_user_agent
        if not user_agent:
            raise NotConfiguredError("set FI_SEC_USER_AGENT to 'Your Name you@example.com'")
        return {"User-Agent": user_agent}

    def fetch_company_tickers(self) -> Any:
        return self.get(TICKERS_URL, dataset="company_tickers")

    def download_bulk_company_facts(self, path: Path) -> Path:
        """Stream SEC's nightly companyfacts.zip (~1.4 GB, every filer) to `path`. Its
        entries are stored in the raw layer one company at a time, not as one file."""
        self.limiter.acquire()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with self.client.stream(
            "GET", BULK_COMPANY_FACTS_URL, headers=self.headers(), timeout=None
        ) as resp:
            if resp.is_error:
                raise ProviderError(f"sec: HTTP {resp.status_code} for the bulk file")
            with tmp.open("wb") as f:
                for chunk in resp.iter_bytes(1 << 20):
                    f.write(chunk)
        tmp.replace(path)
        return path

    def list_insider_datasets(self) -> dict[str, str]:
        """{"2026q2": url, ...} from SEC's data set page. Read from the page because the
        file paths change between quarters (structureddata/ vs datastandardsinnovation/)."""
        html = self.get_bytes(INSIDER_DATASETS_PAGE, dataset="insider_dataset_index").decode()
        return {
            m.group(2): "https://www.sec.gov" + m.group(1)
            for m in re.finditer(r'href="(/files/[^"]*/(\d{4}q\d)_form345\.zip)"', html)
        }

    def list_13f_datasets(self) -> dict[str, str]:
        """{"2026-06-01_2026-08-31": url, ...}: each file covers three months of filings.
        Files before 2024 are named by calendar quarter of filing ("2023q4_form13f.zip")."""
        html = self.get_bytes(THIRTEENF_DATASETS_PAGE, dataset="13f_dataset_index").decode()
        out = {}
        for m in re.finditer(
            r'href="(/files/[^"]*/(\d{2}[a-z]{3}\d{4})-(\d{2}[a-z]{3}\d{4})_form13f\.zip)"', html
        ):
            start = datetime.strptime(m.group(2), "%d%b%Y").date()
            end = datetime.strptime(m.group(3), "%d%b%Y").date()
            out[f"{start}_{end}"] = "https://www.sec.gov" + m.group(1)
        for m in re.finditer(r'href="(/files/[^"]*/(\d{4})q([1-4])_form13f\.zip)"', html):
            year, quarter = int(m.group(2)), int(m.group(3))
            start = date(year, quarter * 3 - 2, 1)
            end = date(year + quarter // 4, quarter % 4 * 3 + 1, 1) - timedelta(days=1)
            out[f"{start}_{end}"] = "https://www.sec.gov" + m.group(1)
        return out

    def fetch_13f_dataset(self, period: str, url: str) -> bytes:
        return self.get_bytes(url, dataset="13f_dataset", key=period)

    def fetch_insider_dataset(self, quarter: str, url: str) -> bytes:
        return self.get_bytes(url, dataset="insider_dataset", key=quarter)

    def fetch_daily_index(self, day: date) -> list[tuple[str, str, str]]:
        """(form type, accession, file path) for every filing on a day; [] on holidays."""
        quarter = (day.month - 1) // 3 + 1
        url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{quarter}/form.{day:%Y%m%d}.idx"
        try:
            text = self.get_bytes(url, dataset="daily_index", key=day.isoformat()).decode("latin-1")
        except NotFoundError:
            return []
        except ProviderError as exc:
            # A day without an index (a holiday) is answered with S3's AccessDenied.
            if "HTTP 403" in str(exc) and "AccessDenied" in str(exc):
                return []
            raise
        return parse_daily_index(text)

    def fetch_submission(self, accession: str, path: str) -> bytes:
        return self.get_bytes(
            f"https://www.sec.gov/Archives/{path}", dataset="form4", key=accession
        )

    def fetch_filing_index(self, cik: int, accession: str) -> Any:
        """The list of files in a filing."""
        url = f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/index.json"
        return self.get(url, dataset="filing_index", key=accession)

    def fetch_xbrl_instance(
        self, cik: int, accession: str, name: str, form: str, filed: date
    ) -> bytes:
        """A filing's XBRL instance document. The raw key carries what the facts need
        (cik, accession, filing date, form), so a rebuild can load it on its own."""
        url = f"{ARCHIVES}/{cik}/{accession.replace('-', '')}/{name}"
        key = f"{cik}|{accession}|{filed.isoformat()}|{form}"
        return self.get_bytes(url, dataset="filing_xbrl", key=key)

    def fetch_submissions_page(self, cik: int, name: str) -> Any:
        """An older page of a filer's submissions (`CIK0000320193-submissions-001.json`)."""
        return self.get(f"/submissions/{name}", dataset="submissions_page", key=f"{cik}|{name}")

    def fetch_submissions(self, cik: int) -> Any:
        """A filer's profile (SIC code, category, fiscal year end) and recent filings."""
        return self.get(f"/submissions/CIK{cik:010d}.json", dataset="submissions", key=str(cik))

    def fetch_company_facts(self, cik: int) -> Any:
        return self.get(
            f"/api/xbrl/companyfacts/CIK{cik:010d}.json", dataset="companyfacts", key=str(cik)
        )


def parse_company_tickers(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """All SEC-registered tickers: [{cik, name, ticker, exchange}]."""
    fields = payload["fields"]
    return [dict(zip(fields, row, strict=True)) for row in payload["data"]]


def parse_company_facts(cik: int, payload: dict[str, Any]):
    """Split a companyfacts payload into filings, concepts and facts.

    Facts reference their filing by accession and their concept by (taxonomy, name).
    """
    filings: dict[str, dict[str, Any]] = {}
    concepts: dict[tuple[str, str], dict[str, Any]] = {}
    facts: list[dict[str, Any]] = []
    for taxonomy, entries in payload.get("facts", {}).items():
        for name, body in entries.items():
            concepts[(taxonomy, name)] = {
                "taxonomy": taxonomy,
                "name": name,
                "label": body.get("label"),
                "description": body.get("description"),
            }
            for unit, unit_facts in body.get("units", {}).items():
                for fact in unit_facts:
                    accession = fact["accn"]
                    filings.setdefault(
                        accession,
                        {
                            "accession": accession,
                            "cik": cik,
                            "form": fact.get("form"),
                            "filed": _date(fact.get("filed")),
                            "fiscal_year": fact.get("fy"),
                            "fiscal_period": fact.get("fp"),
                        },
                    )
                    end = date.fromisoformat(fact["end"])
                    facts.append(
                        {
                            "accession": accession,
                            "concept": (taxonomy, name),
                            "unit": unit,
                            "period_start": _date(fact.get("start")) or end,
                            "period_end": end,
                            "instant": "start" not in fact,
                            "value": fact["val"],
                            "frame": fact.get("frame"),
                        }
                    )
    return list(filings.values()), list(concepts.values()), facts


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_daily_index(text: str) -> list[tuple[str, str, str]]:
    """EDGAR's form.YYYYMMDD.idx: fixed-width rows (form type, company, CIK, date, path)
    after a dashed header line. A filing appears once per filer (issuer and owner), so
    rows are deduplicated by path."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("---")) + 1
    except StopIteration:
        return []
    seen, out = set(), []
    for line in lines[start:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        form, path = parts[0], parts[-1]
        if path in seen:
            continue
        seen.add(path)
        accession = path.rsplit("/", 1)[-1].removesuffix(".txt")
        out.append((form, accession, path))
    return out
