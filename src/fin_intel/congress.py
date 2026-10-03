"""Members of Congress's stock trades: Periodic Transaction Reports (STOCK Act).

Members must report trades over $1,000 by them, their spouse or dependent children within
45 days, as a value range rather than an exact amount.

House: the Clerk publishes a yearly index of filings (a zip with XML) and each report as a
PDF. Electronic filings (DocID starting with 2) are text PDFs, parsed here; paper filings
are scans and only their index entry is kept.
Senate: eFD search returns the reports; electronic ones are HTML tables, paper ones images.

Each report is self-contained (the parsers read the filer and date from the document), so
reports and transactions load in any order.
"""

import hashlib
import html
import io
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import CongressReport, CongressTrade

log = logging.getLogger(__name__)

OWNERS = {"": "self", "SP": "spouse", "JT": "joint", "DC": "child"}
SENATE_OWNERS = {"Self": "self", "Spouse": "spouse", "Joint": "joint", "Child": "child"}
HOUSE_TYPES = {"P": "purchase", "S": "sale", "S (partial)": "sale_partial", "E": "exchange"}
SENATE_TYPES = {
    "Purchase": "purchase",
    "Sale (Full)": "sale",
    "Sale (Partial)": "sale_partial",
    "Sale": "sale",
    "Exchange": "exchange",
}

KEY_FIELDS = (
    "doc_id",
    "owner",
    "ticker",
    "asset_name",
    "trans_type",
    "trans_date",
    "amount_min",
    "amount_max",
)


def _keyed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A stable key per row; identical rows in one report are told apart by occurrence."""
    seen: Counter[str] = Counter()
    out = []
    for row in rows:
        content = "|".join(str(row.get(f)) for f in KEY_FIELDS)
        seen[content] += 1
        digest = hashlib.sha256(f"{content}|{seen[content]}".encode()).hexdigest()[:32]
        out.append({**row, "key": digest})
    return out


def _date(value: str | None) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").date() if value else None
    except ValueError:
        return None


def _amount(text: str) -> tuple[float | None, float | None]:
    """'$1,001 - $15,000' -> (1001, 15000); 'Over $50,000,000' -> (50000001, None)."""
    values = [float(v.replace(",", "")) for v in re.findall(r"\$([\d,]+)", text)]
    if not values:
        return None, None
    if text.strip().lower().startswith("over"):
        return values[0] + 1, None
    return values[0], values[-1]


def _ticker(text: str | None) -> str | None:
    """Our symbol convention: share classes use a dash (BRK.B, BRK/B -> BRK-B)."""
    if not text:
        return None
    text = text.strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-/]{0,9}", text):
        return None
    return text.replace(".", "-").replace("/", "-")


# --- House -----------------------------------------------------------------------------


def parse_house_index(data: bytes) -> list[dict[str, Any]]:
    """Periodic Transaction Reports (FilingType P) from a yearly index zip."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".xml"))
        root = ET.fromstring(archive.read(name))
    out = []
    for m in root.iter("Member"):
        if m.findtext("FilingType") != "P" or not m.findtext("DocID"):
            continue
        doc_id = m.findtext("DocID", "").strip()
        names = (m.findtext(f) or "" for f in ("First", "Last", "Suffix"))
        out.append(
            {
                "doc_id": doc_id,
                "chamber": "house",
                "name": " ".join(n.strip() for n in names if n.strip()),
                "state": (m.findtext("StateDst") or "").strip() or None,
                "filed": _date(m.findtext("FilingDate")),
                "year": int(m.findtext("Year") or 0) or None,
                "electronic": doc_id.startswith("2"),
            }
        )
    return out


# A transaction's fixed columns follow its asset cell: "[ST] S (partial) 03/16/2026
# 03/16/2026 $1,001 - $15,000" (the amount may wrap onto the next line).
_HOUSE_ROW = re.compile(
    r"\[(?P<asset_type>[A-Z]{2})\]\s*"
    r"(?P<type>S \(partial\)|P|S|E)\s+"
    r"(?P<date>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<notified>\d{2}/\d{2}/\d{4})\s*"
    r"(?P<amount>Over \$[\d,]+|\$[\d,]+(?:\s*-\s*\$[\d,]+)?)"
)
# Field labels lose their letters in extraction ("F      S     : New" is Filing Status).
_FIELD = re.compile(r"^[A-Z](?:\s+[A-Z])?\s+:\s?(.*)$")
_HEADER = re.compile(r"ID Owner Asset Transaction.*?\$200\?", re.S)
_BOUNDARY = "<boundary>"
# A row cut by a page break: its fixed columns end page one, and the rest of its asset cell
# (and sometimes the amount's upper bound) starts the next page, after the column header.
_SPLIT_ROW = re.compile(
    r"(?P<head>[^\n]*?)\s(?P<cols>(?:S \(partial\)|P|S|E)\s+\d{2}/\d{2}/\d{4}\s*"
    r"\d{2}/\d{2}/\d{4}\s*(?:Over \$[\d,]+|\$[\d,]+(?:\s*-(?:\s*\$[\d,]+)?)?))\n"
    r"(?:Filing ID #\d+\n)?ID Owner Asset Transaction.*?\$200\?\n"
    r"(?P<tail>[^\n]*?\[[A-Z]{2}\])(?P<rest>[^\n]*)",
    re.S,
)
_CELL_WIDTH = 45  # asset names wrap at about 40 characters; description lines are longer


def _house_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text.replace("\x00", " ")  # glyphs without a text mapping come out as NUL


def parse_house_ptr(doc_id: str, data: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The filer and transactions of one House PTR PDF. A scanned (paper) report has no
    text and yields no transactions."""
    text = _house_text(data)
    name = re.search(r"Name:\s*(?:Hon\.\s*)?(.+)", text)
    state = re.search(r"State/District:\s*(\S+)", text)
    signed = re.search(r"Digitally Signed:.*?,\s*(\d{2}/\d{2}/\d{4})", text)
    report = {
        "doc_id": doc_id,
        "chamber": "house",
        "name": name.group(1).strip() if name else None,
        "state": state.group(1) if state else None,
        "filed": _date(signed.group(1)) if signed else None,
        "electronic": bool(text.strip()),
    }
    text = _SPLIT_ROW.sub(r"\g<head> \g<tail> \g<cols>\g<rest>", text)
    text = _HEADER.sub(f"\n{_BOUNDARY}\n", text)
    text = re.sub(r"^(Filing ID #\d+|\* For the complete list.*)$", _BOUNDARY, text, flags=re.M)

    matches = list(_HOUSE_ROW.finditer(text))
    rows = []
    previous_end = 0
    for i, m in enumerate(matches):
        before = [ln.strip() for ln in text[previous_end : m.start()].split("\n")]
        # The asset cell: the line holding "[XX]" plus the short lines above it.
        asset_lines = [before.pop()] if before else []
        while before and len(asset_lines) < 4:
            line = before[-1]
            if not line or line == _BOUNDARY or _FIELD.match(line) or len(line) > _CELL_WIDTH:
                break
            asset_lines.insert(0, before.pop())
        if rows:  # what's left above is the previous transaction's fields
            rows[-1]["comment"] = _description(before)
        asset = " ".join(ln for ln in asset_lines if ln and ln != _BOUNDARY)
        owner = re.match(r"^(SP|JT|DC)\s+", asset)
        if owner:
            asset = asset[owner.end() :]
        ticker = re.search(r"\(([^()]+)\)\s*$", asset)
        amount_min, amount_max = _amount(m.group("amount"))
        rows.append(
            {
                "doc_id": doc_id,
                "chamber": "house",
                "owner": OWNERS[owner.group(1) if owner else ""],
                "ticker": _ticker(ticker.group(1)) if ticker else None,
                "asset_name": (asset[: ticker.start()] if ticker else asset).strip() or None,
                "asset_type": m.group("asset_type"),
                "trans_type": HOUSE_TYPES[m.group("type")],
                "trans_date": _date(m.group("date")),
                "notified": _date(m.group("notified")),
                "amount_min": amount_min,
                "amount_max": amount_max,
                "comment": None,
            }
        )
        previous_end = m.end()
        if i == len(matches) - 1:
            after = text[m.end() :].split(_BOUNDARY)[0]
            rows[-1]["comment"] = _description([ln.strip() for ln in after.split("\n")])
    return report, _keyed(rows)


def _description(lines: list[str]) -> str | None:
    """The 'Description:' field among a transaction's trailing lines, with its wrapped
    continuation lines."""
    out: list[str] = []
    for line in lines:
        field = _FIELD.match(line)
        if field and line.startswith("D"):
            out = [field.group(1)]
        elif field or line == _BOUNDARY:
            if out:
                break
        elif out and line:
            out.append(line)
    return " ".join(out).strip() or None


# --- Senate ----------------------------------------------------------------------------


def parse_senate_search(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Reports from one page of eFD search results."""
    out = []
    for first, last, _office, link, filed in payload.get("data") or []:
        m = re.search(r'href="/search/view/(ptr|paper)/([^/"]+)/?"', link)
        if not m:
            continue
        out.append(
            {
                "doc_id": m.group(2),
                "chamber": "senate",
                "name": f"{_clean(first)} {_clean(last)}".strip(),
                "state": None,
                "filed": _date(filed),
                "year": None,
                "electronic": m.group(1) == "ptr",
            }
        )
    return out


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", value or ""))).strip()


def parse_senate_ptr(doc_id: str, page: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The filer and transactions of one electronic Senate PTR page."""
    text = page.decode("utf-8", errors="replace")
    name = re.search(r"The Honorable\s+([^<(]+)", text)
    filed = re.search(r"Filed\s+(\d{2}/\d{2}/\d{4})", text)
    report = {
        "doc_id": doc_id,
        "chamber": "senate",
        "name": _clean(name.group(1)) if name else None,
        "filed": _date(filed.group(1)) if filed else None,
        "electronic": True,
    }
    body = re.search(r"<tbody>(.*?)</tbody>", text, re.S)
    rows = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body.group(1) if body else "", re.S):
        cells = [_clean(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) < 9:
            continue
        _n, when, owner, ticker, asset, asset_type, kind, amount, comment = cells[:9]
        amount_min, amount_max = _amount(amount)
        rows.append(
            {
                "doc_id": doc_id,
                "chamber": "senate",
                "owner": SENATE_OWNERS.get(owner, owner.lower() or None),
                "ticker": _ticker(ticker) if ticker != "--" else None,
                "asset_name": asset or None,
                "asset_type": asset_type or None,
                "trans_type": SENATE_TYPES.get(kind, kind.lower() or None),
                "trans_date": _date(when),
                "notified": None,
                "amount_min": amount_min,
                "amount_max": amount_max,
                "comment": comment if comment and comment != "--" else None,
            }
        )
    rows.reverse()  # the page lists the last transaction first
    return report, _keyed(rows)


# --- loading ---------------------------------------------------------------------------

INDEX_COLUMNS = ["name", "state", "filed", "year", "electronic"]


def load_index(session: Session, reports: list[dict[str, Any]]) -> int:
    """Index entries: who filed what, when (fills only the index's own columns)."""
    return upsert(session, CongressReport, reports, key=["doc_id"], update=INDEX_COLUMNS)


def load_report(session: Session, report: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    """One parsed report: its transactions replace any loaded before."""
    session.query(CongressTrade).filter(CongressTrade.doc_id == report["doc_id"]).delete()
    existing = session.get(CongressReport, report["doc_id"])
    meta = {k: v for k, v in report.items() if v is not None}
    if existing is None:
        session.add(CongressReport(**meta, transactions=len(rows)))
    else:
        for k, v in meta.items():
            if getattr(existing, k) is None or k == "electronic":
                setattr(existing, k, v)
        existing.transactions = len(rows)
    session.flush()
    return upsert(session, CongressTrade, [r for r in rows if r["trans_date"]], key=["key"])


def pending_reports(session: Session, chamber: str) -> list[CongressReport]:
    """Electronic reports listed in an index but not parsed yet."""
    return list(
        session.scalars(
            select(CongressReport)
            .where(
                CongressReport.chamber == chamber,
                CongressReport.electronic.is_(True),
                CongressReport.transactions.is_(None),
            )
            .order_by(CongressReport.filed)
        )
    )


# --- views -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Trade:
    name: str | None
    chamber: str
    state: str | None
    filed: date | None
    trans_date: date
    owner: str | None
    ticker: str | None
    asset_name: str | None
    asset_type: str | None
    trans_type: str | None
    amount_min: float | None
    amount_max: float | None
    comment: str | None
    doc_id: str


def trades(
    session: Session,
    *,
    member: str | None = None,
    ticker: str | None = None,
    since: date | None = None,
    trans_type: str | None = None,
    limit: int = 200,
) -> list[Trade]:
    """Recent trades, newest first, filtered by member name, ticker, date or type."""
    query = select(CongressTrade, CongressReport).join(
        CongressReport, CongressReport.doc_id == CongressTrade.doc_id
    )
    if member:
        query = query.where(CongressReport.name.ilike(f"%{member}%"))
    if ticker:
        query = query.where(CongressTrade.ticker == ticker.upper().replace(".", "-"))
    if since:
        query = query.where(CongressTrade.trans_date >= since)
    if trans_type:
        query = query.where(CongressTrade.trans_type.startswith(trans_type))
    query = query.order_by(
        CongressTrade.trans_date.desc(), CongressReport.filed.desc(), CongressTrade.ticker
    )
    return [
        Trade(
            name=r.name,
            chamber=r.chamber,
            state=r.state,
            filed=r.filed,
            trans_date=t.trans_date,
            owner=t.owner,
            ticker=t.ticker,
            asset_name=t.asset_name,
            asset_type=t.asset_type,
            trans_type=t.trans_type,
            amount_min=t.amount_min,
            amount_max=t.amount_max,
            comment=t.comment,
            doc_id=t.doc_id,
        )
        for t, r in session.execute(query.limit(limit))
    ]


@dataclass(frozen=True)
class Popular:
    ticker: str
    members: int
    purchases: int
    sales: int
    amount_min: float  # sum of the lower bounds of the reported ranges
    names: list[str]


def most_traded(session: Session, days: int = 90, as_of: date | None = None) -> list[Popular]:
    """Tickers traded by the most distinct members in the window (by transaction date)."""
    as_of = as_of or date.today()
    rows = session.execute(
        select(CongressTrade, CongressReport.name)
        .join(CongressReport, CongressReport.doc_id == CongressTrade.doc_id)
        .where(
            CongressTrade.ticker.is_not(None),
            CongressTrade.trans_date > as_of - timedelta(days=days),
            CongressTrade.trans_date <= as_of,
        )
    )
    by_ticker: dict[str, list[tuple[CongressTrade, str | None]]] = defaultdict(list)
    for t, name in rows:
        assert t.ticker is not None  # filtered above
        by_ticker[t.ticker].append((t, name))
    out = [
        Popular(
            ticker=ticker,
            members=len({n for _, n in items}),
            purchases=sum(1 for t, _ in items if t.trans_type == "purchase"),
            sales=sum(1 for t, _ in items if (t.trans_type or "").startswith("sale")),
            amount_min=sum(t.amount_min or 0 for t, _ in items),
            names=sorted({n or "?" for _, n in items}),
        )
        for ticker, items in by_ticker.items()
    ]
    return sorted(out, key=lambda p: (-p.members, -p.amount_min))
