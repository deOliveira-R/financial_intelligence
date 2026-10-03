"""Insider transactions (SEC Forms 3, 4, 5): who at a company bought or sold its stock.

Two sources produce the same rows:
- SEC's quarterly Insider Transactions Data Sets (flattened Forms 3/4/5, back to 2006),
  published a couple of weeks after each quarter.
- Each Form 4's XML, from EDGAR's daily index, for the days since the latest data set
  (insiders must file within two business days, so this is what makes signals timely).
A row's key is a hash of its content within its filing, so the same transaction arriving
from both sources is stored once.

Transaction codes: P open-market purchase, S open-market sale, A grant or award, M option
exercise, F shares withheld for taxes, G gift, D disposition to the issuer, C conversion.
Purchases (P) outside 10b5-1 plans are the informative ones: insiders sell for many
reasons, but buy for one.
"""

import csv
import hashlib
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import InsiderTransaction

KEY_FIELDS = (
    "accession",
    "owner_cik",
    "trans_date",
    "trans_code",
    "acquired_disposed",
    "shares",
    "price",
    "shares_after",
    "direct_indirect",
    "security_title",
)


def _key(row: dict[str, Any], occurrence: int) -> str:
    content = "|".join(str(row.get(f) if row.get(f) is not None else "") for f in KEY_FIELDS)
    return hashlib.sha256(f"{content}|{occurrence}".encode()).hexdigest()[:32]


def _keyed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach a stable key; identical rows in one filing are told apart by occurrence."""
    seen: Counter[str] = Counter()
    out = []
    for row in rows:
        content = "|".join(str(row.get(f)) for f in KEY_FIELDS)
        seen[content] += 1
        out.append({**row, "key": _key(row, seen[content])})
    return out


def _num(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _relationship(*flags: tuple[str, bool]) -> str:
    return ",".join(name for name, on in flags if on) or "Other"


# --- quarterly data sets ---------------------------------------------------------------


def parse_dataset(data: bytes) -> list[dict[str, Any]]:
    """Non-derivative transactions from one quarterly Form 3/4/5 data set zip."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:

        def table(name: str) -> list[dict[str, str]]:
            with archive.open(name) as f:
                text = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
                return list(csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE))

        submissions = {r["ACCESSION_NUMBER"]: r for r in table("SUBMISSION.tsv")}
        owners: dict[str, dict[str, str]] = {}
        for r in table("REPORTINGOWNER.tsv"):
            owners.setdefault(r["ACCESSION_NUMBER"], r)  # first reporting owner
        transactions = table("NONDERIV_TRANS.tsv")

    transactions.sort(key=lambda r: (r["ACCESSION_NUMBER"], int(r.get("NONDERIV_TRANS_SK") or 0)))
    rows = []
    for t in transactions:
        accession = t["ACCESSION_NUMBER"]
        sub, owner = submissions.get(accession), owners.get(accession, {})
        if sub is None:
            continue
        rows.append(
            {
                "accession": accession,
                "form_type": sub.get("DOCUMENT_TYPE"),
                "filing_date": _date(sub.get("FILING_DATE")),
                "issuer_cik": int(sub["ISSUERCIK"]),
                "issuer_symbol": (sub.get("ISSUERTRADINGSYMBOL") or "").strip().upper() or None,
                "owner_cik": int(owner["RPTOWNERCIK"]) if owner.get("RPTOWNERCIK") else None,
                "owner_name": owner.get("RPTOWNERNAME"),
                "relationship": owner.get("RPTOWNER_RELATIONSHIP") or "Other",
                "owner_title": owner.get("RPTOWNER_TITLE") or None,
                "security_title": t.get("SECURITY_TITLE"),
                "trans_date": _date(t.get("TRANS_DATE")),
                "trans_code": (t.get("TRANS_CODE") or "").strip() or None,
                "acquired_disposed": (t.get("TRANS_ACQUIRED_DISP_CD") or "").strip() or None,
                "shares": _num(t.get("TRANS_SHARES")),
                "price": _num(t.get("TRANS_PRICEPERSHARE")),
                "shares_after": _num(t.get("SHRS_OWND_FOLWNG_TRANS")),
                "direct_indirect": (t.get("DIRECT_INDIRECT_OWNERSHIP") or "").strip() or None,
                "plan_10b5_1": sub.get("AFF10B5ONE") == "1",
            }
        )
    by_filing: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_filing[r["accession"]].append(r)
    return [r for filing in by_filing.values() for r in _keyed(filing)]


# --- individual Form 4 XML (daily path) ------------------------------------------------

_XML = re.compile(rb"<XML>\s*(.*?)\s*</XML>", re.S)


def _text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    found = node.find(path)
    if found is None:
        return None
    value = found.findtext("value") if found.find("value") is not None else found.text
    return value.strip() if value and value.strip() else None


def parse_form4(accession: str, filing_date: date, submission: bytes) -> list[dict[str, Any]]:
    """Non-derivative transactions from one EDGAR submission (.txt with an embedded
    ownershipDocument XML), in the same shape as parse_dataset."""
    match = _XML.search(submission)
    if not match:
        return []
    doc = ET.fromstring(match.group(1))
    issuer = doc.find("issuer")
    owner = doc.find("reportingOwner")
    rel = owner.find("reportingOwnerRelationship") if owner is not None else None

    def flag(name: str) -> bool:
        return (_text(rel, name) or "").lower() in ("1", "true")

    plan = (_text(doc, "aff10b5One") or "").lower() in ("1", "true")
    rows = []
    for t in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        rows.append(
            {
                "accession": accession,
                "form_type": _text(doc, "documentType"),
                "filing_date": filing_date,
                "issuer_cik": int(_text(issuer, "issuerCik") or 0),
                "issuer_symbol": (_text(issuer, "issuerTradingSymbol") or "").upper() or None,
                "owner_cik": int(c)
                if (c := _text(owner, "reportingOwnerId/rptOwnerCik"))
                else None,
                "owner_name": _text(owner, "reportingOwnerId/rptOwnerName"),
                "relationship": _relationship(
                    ("Director", flag("isDirector")),
                    ("Officer", flag("isOfficer")),
                    ("TenPercentOwner", flag("isTenPercentOwner")),
                    ("Other", flag("isOther")),
                ),
                "owner_title": _text(rel, "officerTitle"),
                "security_title": _text(t, "securityTitle"),
                "trans_date": _date(_text(t, "transactionDate")),
                "trans_code": _text(t, "transactionCoding/transactionCode"),
                "acquired_disposed": _text(t, "transactionAmounts/transactionAcquiredDisposedCode"),
                "shares": _num(_text(t, "transactionAmounts/transactionShares")),
                "price": _num(_text(t, "transactionAmounts/transactionPricePerShare")),
                "shares_after": _num(
                    _text(t, "postTransactionAmounts/sharesOwnedFollowingTransaction")
                ),
                "direct_indirect": _text(t, "ownershipNature/directOrIndirectOwnership"),
                "plan_10b5_1": plan,
            }
        )
    return _keyed(rows)


def load(session: Session, rows: list[dict[str, Any]]) -> int:
    return upsert(session, InsiderTransaction, [r for r in rows if r["trans_date"]], key=["key"])


# --- signals ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClusterBuy:
    issuer_cik: int
    symbol: str | None
    insiders: int
    purchases: int
    shares: float
    value: float
    first: date
    last: date
    names: list[str]


def cluster_buys(
    session: Session, days: int = 30, min_insiders: int = 3, as_of: date | None = None
) -> list[ClusterBuy]:
    """Companies where several distinct insiders bought on the open market (code P, not
    under a 10b5-1 plan) within the window: a well-studied bullish signal."""
    as_of = as_of or date.today()
    rows = session.execute(
        select(InsiderTransaction).where(
            InsiderTransaction.trans_code == "P",
            InsiderTransaction.plan_10b5_1.is_(False),
            InsiderTransaction.trans_date > as_of - timedelta(days=days),
            InsiderTransaction.trans_date <= as_of,
        )
    ).scalars()
    by_issuer: dict[int, list[InsiderTransaction]] = defaultdict(list)
    for r in rows:
        by_issuer[r.issuer_cik].append(r)
    out = []
    for cik, txs in by_issuer.items():
        owners = {t.owner_cik or t.owner_name for t in txs}
        if len(owners) < min_insiders:
            continue
        out.append(
            ClusterBuy(
                issuer_cik=cik,
                symbol=next((t.issuer_symbol for t in txs if t.issuer_symbol), None),
                insiders=len(owners),
                purchases=len(txs),
                shares=sum(t.shares or 0 for t in txs),
                value=sum((t.shares or 0) * (t.price or 0) for t in txs),
                first=min(t.trans_date for t in txs),
                last=max(t.trans_date for t in txs),
                names=sorted({t.owner_name or "?" for t in txs}),
            )
        )
    return sorted(out, key=lambda c: (-c.insiders, -c.value))


def latest_transaction_date(session: Session) -> date | None:
    return session.scalar(select(func.max(InsiderTransaction.filing_date)))
