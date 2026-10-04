"""Japanese filings (EDINET): figures that need assembling before they map to statements.

J-GAAP and Japan's IFRS taxonomy tag most statement lines directly (statements.py maps
them), but two figures metrics need are spread over several lines:

- interest-bearing debt: short-term loans, long-term loans and their current portion,
  bonds and their current portion, commercial paper, summed per balance sheet date;
- shares outstanding: issued shares minus treasury shares at the fiscal year end.

Both are added as `fin-intel` facts dated like their components.
"""

from collections import defaultdict
from typing import Any

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
