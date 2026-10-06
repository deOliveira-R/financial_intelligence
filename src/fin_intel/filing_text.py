"""Filing text for language-model analysis: the narrative sections of periodic reports and
earnings press releases, as plain text.

- 10-K: Item 1A risk factors, Item 7 MD&A.
- 10-Q: Part I Item 2 MD&A, Part II Item 1A risk factor updates.
- 8-K with Item 2.02 (results of operations): the press release and other EX-99 exhibits.

Sections are found by their headings; the table of contents repeats every heading, so the
longest span between a section's heading and the next one wins. Text is searchable with
SQLite's full-text index (`filing_text_fts`).
"""

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import FilingText

# (form, section) -> (start heading, end headings)
_ITEM = r"^\s*item\s*"
SECTIONS: dict[tuple[str, str], tuple[str, str]] = {
    ("10-K", "risk_factors"): (
        _ITEM + r"1a\b\.?\s*[-–—:.]?\s*risk\s+factors",
        _ITEM + r"(1b|1c|2)\b",
    ),
    ("10-K", "mdna"): (
        _ITEM + r"7\b\.?\s*[-–—:.]?\s*management",
        _ITEM + r"(7a|8)\b",
    ),
    ("10-Q", "mdna"): (
        _ITEM + r"2\b\.?\s*[-–—:.]?\s*management",
        _ITEM + r"(3|4)\b",
    ),
    ("10-Q", "risk_factors"): (
        _ITEM + r"1a\b\.?\s*[-–—:.]?\s*risk\s+factors",
        _ITEM + r"(2|3|4|5|6)\b",
    ),
}
# Some 10-Ks (Exxon's) put MD&A in a "Financial Section" under its bare title, with Item 7
# only pointing there.
FALLBACKS: dict[tuple[str, str], tuple[str, str]] = {
    ("10-K", "mdna"): (
        r"^\s*management['’]s\s+discussion\s+and\s+analysis\s+of\s+financial\s+condition"
        r"\s+and\s+results\s+of\s+operations\s*$",
        r"^\s*(quantitative\s+and\s+qualitative\s+disclosures?\s+about\s+market\s+risk"
        r"|management['’]s\s+report\s+on\s+internal\s+control"
        r"|report\s+of\s+independent\s+registered)",
    ),
}
MIN_SECTION = 500  # characters: shorter spans are table-of-contents entries or references
MAX_EXHIBITS = 3
MAX_EXHIBIT_CHARS = 200_000
FORMS = ("10-K", "10-Q")
RESULTS_ITEM = "2.02"  # 8-K: results of operations and financial condition

_BLOCK = re.compile(r"<\s*/?\s*(p|div|br|tr|li|h[1-6]|table|section|article)\b[^>]*>", re.I)
_CELL = re.compile(r"<\s*/\s*t[dh]\s*>", re.I)


def to_text(document: bytes) -> str:
    """Readable text from an EDGAR HTML document (inline XBRL included)."""
    text = document.decode("utf-8", errors="replace")
    text = re.sub(r"<ix:header>.*?</ix:header>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<(script|style|head)\b.*?</\1\s*>", " ", text, flags=re.S | re.I)
    text = _CELL.sub("\t", text)
    text = _BLOCK.sub("\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    lines = (re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def section(text: str, form: str, name: str) -> str | None:
    """A section's text, or None when its heading can't be found reliably."""
    key = (form.removesuffix("/A"), name)
    found = _longest(text, *SECTIONS[key])
    if found is None and key in FALLBACKS:
        found = _longest(text, *FALLBACKS[key])
    return found


def _longest(text: str, start_pattern: str, end_pattern: str) -> str | None:
    starts = [m.start() for m in re.finditer(start_pattern, text, re.I | re.M)]
    ends = [m.start() for m in re.finditer(end_pattern, text, re.I | re.M)]
    best = None
    for s in starts:
        end = next((e for e in ends if e > s + 20), len(text))
        if best is None or end - s > best[1] - best[0]:
            best = (s, end)
    if best is None or best[1] - best[0] < MIN_SECTION:
        return None
    return text[best[0] : best[1]].strip()


def sections_for(form: str) -> list[str]:
    return [name for f, name in SECTIONS if f == form.removesuffix("/A")]


@dataclass
class Exhibit:
    document: str
    kind: str  # EX-99.1 ...


def exhibits(index_page: bytes) -> list[Exhibit]:
    """EX-99 documents (press releases, supplements) from a filing's index page."""
    page = index_page.decode("utf-8", errors="replace")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [
            re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        ]
        link = re.search(r'href="(/Archives/[^"]+\.(?:htm|html|txt))"', row)
        if len(cells) >= 4 and cells[3].upper().startswith("EX-99") and link:
            out.append(Exhibit(link.group(1).rsplit("/", 1)[-1], cells[3].upper()))
    return out[:MAX_EXHIBITS]


def document_key(cik: int, accession: str, form: str, filed: str, period: str, kind: str) -> str:
    """Raw key of a fetched document: `kind` is "report" or the exhibit type (EX-99.1)."""
    return f"{cik}|{accession}|{form}|{filed}|{period}|{kind}"


def load(session: Session, key: str, body: bytes) -> int:
    """A fetched document's sections (a report) or its whole text (an exhibit)."""
    cik, accession, form, filed, period, kind = key.split("|")
    content = to_text(body)
    if kind == "report":
        found = {name: section(content, form, name) for name in sections_for(form)}
    else:
        found = {kind: content[:MAX_EXHIBIT_CHARS] or None}
    rows = [
        {
            "accession": accession,
            "section": name,
            "cik": int(cik),
            "form": form,
            "filed": date.fromisoformat(filed),
            "period": date.fromisoformat(period) if period else None,
            "text": value,
        }
        for name, value in found.items()
        if value
    ]
    return upsert(session, FilingText, rows, key=["accession", "section"])


def wanted(submissions: dict[str, Any], since: date) -> list[dict[str, str]]:
    """10-Ks, 10-Qs and earnings 8-Ks filed since `since`, from a submissions response."""
    recent = (submissions.get("filings") or {}).get("recent") or {}
    out = []
    for i, form in enumerate(recent.get("form") or []):
        filed = recent["filingDate"][i]
        items = (recent.get("items") or [""] * (i + 1))[i] or ""
        if filed < since.isoformat():
            continue
        if form in FORMS or (form == "8-K" and RESULTS_ITEM in items.split(",")):
            out.append(
                {
                    "accession": recent["accessionNumber"][i],
                    "form": form,
                    "filed": filed,
                    "period": recent.get("reportDate", [""] * (i + 1))[i] or "",
                    "document": recent["primaryDocument"][i],
                }
            )
    return out


@dataclass
class Hit:
    accession: str
    section: str
    cik: int
    form: str
    filed: date
    snippet: str


def search(
    session: Session,
    query: str,
    ciks: list[int] | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[Hit]:
    """Full-text search (SQLite FTS5 syntax: words, "exact phrases", OR, NEAR), best
    matches first, with a snippet around the match."""
    where, params = ["filing_text_fts MATCH :q"], {"q": query, "n": limit}
    if ciks:
        where.append(f"f.cik IN ({','.join(str(int(c)) for c in ciks)})")
    if since:
        where.append("f.filed >= :since")
        params["since"] = since.isoformat()
    rows = session.execute(
        text(
            "SELECT f.accession, f.section, f.cik, f.form, f.filed, "
            "snippet(filing_text_fts, 0, '[', ']', ' … ', 24) "
            "FROM filing_text_fts JOIN filing_texts f ON f.rowid = filing_text_fts.rowid "
            f"WHERE {' AND '.join(where)} ORDER BY rank LIMIT :n"
        ),
        params,
    ).all()
    return [
        Hit(a, s, c, f, d if isinstance(d, date) else date.fromisoformat(d), snip)
        for a, s, c, f, d, snip in rows
    ]


def get(session: Session, accession: str, name: str | None = None) -> list[FilingText]:
    stmt = select(FilingText).where(FilingText.accession == accession)
    if name:
        stmt = stmt.where(FilingText.section == name)
    return list(session.scalars(stmt))
