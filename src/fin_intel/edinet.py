"""Japanese filings (EDINET): figures that need assembling before they map to statements.

J-GAAP and Japan's IFRS taxonomy tag most statement lines directly (statements.py maps
them), but two figures metrics need are spread over several lines:

- interest-bearing debt: short-term loans, long-term loans and their current portion,
  bonds and their current portion, commercial paper, summed per balance sheet date;
- shares outstanding: issued shares minus treasury shares at the fiscal year end.

Both are added as `fin-intel` facts dated like their components.
"""

import logging
from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from fin_intel import registry, world, xbrl
from fin_intel.edgar import load_company_facts
from fin_intel.pipeline import tracked
from fin_intel.providers import EdinetProvider
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

DEBT_JGAAP = [
    "ShortTermLoansPayable",
    "CurrentPortionOfLongTermLoansPayable",
    "LongTermLoansPayable",
    "BondsPayable",
    "CurrentPortionOfBonds",
    "CommercialPapersLiabilities",
]
# IFRS filers tag either combined "bonds and borrowings" or the parts.
DEBT_IFRS_COMBINED = ["BondsAndBorrowingsCLIFRS", "BondsAndBorrowingsNCLIFRS"]
DEBT_IFRS_PARTS = [
    "BorrowingsCLIFRS",
    "BorrowingsNCLIFRS",
    "BondsPayableNCLIFRS",
    "BondsPayableCLIFRS",
    "CurrentPortionOfLongTermBorrowingsCLIFRS",
    "CommercialPapersCLIFRS",
]
ISSUED = "NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc"
TREASURY = "TotalNumberOfSharesHeldTreasurySharesEtc"


def _instants(facts: dict, taxonomy: str, concept: str) -> dict[tuple[str, str], dict]:
    """(unit, end) -> fact, for a concept's instant facts."""
    out = {}
    for unit, entries in ((facts.get(taxonomy) or {}).get(concept) or {}).get("units", {}).items():
        for f in entries:
            if "start" not in f:
                out[(unit, f["end"])] = f
    return out


def _sum(
    facts: dict, taxonomy: str, concepts: list[str]
) -> dict[tuple[str, str], tuple[float, dict]]:
    totals: dict[tuple[str, str], list] = defaultdict(lambda: [0.0, None])
    for concept in concepts:
        for key, f in _instants(facts, taxonomy, concept).items():
            totals[key][0] += f["val"]
            totals[key][1] = f
    return {k: (v[0], v[1]) for k, v in totals.items()}


def add_derived(payload: dict[str, Any]) -> dict[str, Any]:
    """The payload with `fin-intel` InterestBearingDebt and SharesOutstanding facts."""
    facts = payload["facts"]
    debt = _sum(facts, "jppfs", DEBT_JGAAP)
    ifrs = _sum(facts, "jpigp", DEBT_IFRS_COMBINED) or _sum(facts, "jpigp", DEBT_IFRS_PARTS)
    debt.update(ifrs)  # an IFRS filer's own figures win
    derived: dict[str, dict[str, list]] = {}
    for (unit, _), (total, sample) in debt.items():
        entry = {k: v for k, v in sample.items() if k != "val"} | {"val": total}
        derived.setdefault("InterestBearingDebt", {}).setdefault(unit, []).append(entry)

    issued = _instants(facts, "jpcrp", ISSUED)
    treasury = _instants(facts, "jpcrp", TREASURY)
    for (unit, end), f in issued.items():
        held = treasury.get((unit, end), {}).get("val", 0.0)
        entry = {k: v for k, v in f.items() if k != "val"} | {"val": f["val"] - held}
        derived.setdefault("SharesOutstanding", {}).setdefault(unit, []).append(entry)

    if derived:
        facts["fin-intel"] = {concept: {"units": units} for concept, units in derived.items()}
    return payload


# --- loading and syncs ---


def load_edinet_instance(session: Session, key: str, body: bytes) -> int:
    """One EDINET report's facts. The raw key carries the document id, filer, type and
    filing date: `S100YE9I|E00776|120|2026-06-19`."""
    from fin_intel.providers.edinet import DOC_TYPES

    doc_id, edinet_code, doc_type, filed = key.split("|")
    payload = xbrl.parse_instance(
        body, f"edinet:{doc_id}", DOC_TYPES.get(doc_type), date.fromisoformat(filed)
    )

    dei = payload.pop("dei", {})
    add_derived(payload)
    year_end = dei.get("CurrentFiscalYearEndDateDEI") or ""
    code = (dei.get("SecurityCodeDEI") or "").strip()
    cik = world.ensure_issuer(
        session,
        "edinet",
        edinet_code,
        payload.get("entityName"),
        home_ticker=code[:4] or None,
        fiscal_month=int(year_end[5:7]) if len(year_end) >= 7 else None,
    )
    if not payload["facts"]:
        return 0
    return load_company_facts(session, cik, payload, supplement=True)


def edinet_reports(documents: Any) -> list[dict[str, Any]]:
    """Listed companies' annual, quarterly and half-year reports with XBRL in a day's list."""
    from fin_intel.providers.edinet import DOC_TYPES

    return [
        r
        for r in (documents or {}).get("results") or []
        if r.get("docTypeCode") in DOC_TYPES and r.get("secCode") and r.get("xbrlFlag") == "1"
    ]


def sync_edinet_day(session: Session, provider: EdinetProvider, day: date) -> int:
    """Every report a day's list names that isn't loaded yet."""
    with tracked(session, "edinet", "documents", day.isoformat()) as result:
        session.commit()
        reports = edinet_reports(provider.fetch_documents(day))
        store = provider.raw_store
        done = (
            {k.split("|")[0] for k in store.latest_hashes("edinet", "instance") if k}
            if store
            else set()
        )
        rows = 0
        for r in reports:
            if r["docID"] in done:
                continue
            filed = (r.get("submitDateTime") or day.isoformat())[:10]
            key = f"{r['docID']}|{r['edinetCode']}|{r['docTypeCode']}|{filed}"
            session.commit()  # fetch-then-load
            body = provider.fetch_instance(r["docID"], key)
            if body is not None:
                rows += load_edinet_instance(session, key, body)
        result["rows"] = rows
    return result["rows"]


registry.register(
    Dataset("edinet", "documents", lambda s, k, p, t: len(edinet_reports(p))),
    Dataset(
        "edinet",
        "instance",
        lambda s, k, p, t: load_edinet_instance(s, k, p),
        ("fundamentals",),
        payload="bytes",
    ),
)
