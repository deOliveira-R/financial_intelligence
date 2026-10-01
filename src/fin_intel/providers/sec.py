from datetime import date
from typing import Any

from fin_intel.config import get_settings
from fin_intel.providers.base import NotConfiguredError, Provider
from fin_intel.providers.ratelimit import SECOND, Limit

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"


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
