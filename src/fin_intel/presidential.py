"""Presidential documents from the Federal Register since 1994: executive orders,
proclamations (tariffs under Sections 232 and 301 are proclaimed), memoranda,
determinations and notices (national emergencies behind sanctions), with full text.

A policy signal like bills and lobbying, but faster: an order or proclamation acts on
signing. Documents count as known from their signing date (the White House posts them the
same day; Federal Register publication follows within days). The Federal Register gives
executive orders no topics, so research works from the text: full-text search over every
document, and links to what each amends or revokes (`disposition`).
"""

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import PresidentialDocument, PresidentialText

KINDS = ("executive_order", "proclamation", "memorandum", "determination", "notice")
FIRST_YEAR = 1994


def _date(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def load_list(session: Session, payload: dict[str, Any]) -> int:
    rows = []
    for d in payload.get("results") or []:
        if not d.get("document_number"):
            continue
        number = d.get("executive_order_number") or d.get("proclamation_number")
        rows.append(
            {
                "document_number": d["document_number"],
                "kind": d.get("presidential_document_type") or "other",
                "number": str(number) if number else None,
                "title": d.get("title"),
                "president": (d.get("president") or {}).get("name"),
                "signed": _date(d.get("signing_date")),
                "published": _date(d.get("publication_date")),
                "disposition": d.get("disposition_notes"),
            }
        )
    return upsert(session, PresidentialDocument, rows, key=["document_number"])


def to_text(body: bytes) -> str:
    """The plain text inside the Federal Register's <pre> wrapper."""
    page = body.decode("utf-8", errors="replace")
    inner = re.search(r"<pre>(.*)</pre>", page, re.S)
    plain = html.unescape(re.sub(r"<[^>]+>", "", inner.group(1) if inner else page))
    lines = (line.strip() for line in plain.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def load_text(session: Session, document_number: str, body: bytes) -> int:
    content = to_text(body)
    if not content:
        return 0
    return upsert(
        session,
        PresidentialText,
        [{"document_number": document_number, "text": content}],
        key=["document_number"],
    )


def missing_text(session: Session, payloads: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(document number, text URL) of listed documents without stored text."""
    have = set(session.scalars(select(PresidentialText.document_number)))
    return [
        (d["document_number"], d["raw_text_url"])
        for p in payloads
        for d in p.get("results") or []
        if d.get("raw_text_url") and d.get("document_number") not in have
    ]


@dataclass
class Hit:
    document_number: str
    kind: str
    number: str | None
    title: str | None
    president: str | None
    signed: date | None
    snippet: str


def search(
    session: Session,
    query: str,
    kind: str | None = None,
    since: date | None = None,
    limit: int = 20,
) -> list[Hit]:
    """Full-text search (FTS5 syntax), best matches first, with snippets."""
    where, params = ["presidential_text_fts MATCH :q"], {"q": query, "n": limit}
    if kind:
        where.append("d.kind = :kind")
        params["kind"] = kind
    if since:
        where.append("d.signed >= :since")
        params["since"] = since.isoformat()
    rows = session.execute(
        text(
            "SELECT d.document_number, d.kind, d.number, d.title, d.president, d.signed, "
            "snippet(presidential_text_fts, 0, '[', ']', ' … ', 24) "
            "FROM presidential_text_fts "
            "JOIN presidential_texts t ON t.rowid = presidential_text_fts.rowid "
            "JOIN presidential_documents d ON d.document_number = t.document_number "
            f"WHERE {' AND '.join(where)} ORDER BY rank LIMIT :n"
        ),
        params,
    ).all()
    return [
        Hit(
            n,
            k,
            num,
            title,
            pres,
            s if isinstance(s, date) or s is None else date.fromisoformat(s),
            snip,
        )
        for n, k, num, title, pres, s, snip in rows
    ]
