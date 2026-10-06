"""Institutional holdings from SEC Form 13F: what managers with $100M+ held each quarter.

Source: SEC's Form 13F Data Sets (flattened 13F filings, three months per file). A filer
often reports one security in several rows (per subsidiary or sub-manager: Berkshire lists
Apple 13 times); positions are summed per filer, quarter, CUSIP and option type. Values
are in US dollars.

Amendments are applied in filing order: an original report (13F-HR) or a RESTATEMENT
amendment replaces the filer's positions for that quarter; a NEW HOLDINGS amendment adds
to them. Holdings are as of the quarter end and filed up to 45 days later.

Before 2023-01-03 values were reported in thousands of dollars; they're converted.

History: a quarter has ~2.4M positions, mostly from thousands of small filers. Data sets
covering filings before FULL_SINCE keep only filers with at least HISTORY_MIN_AUM in that
report (96% of reported assets) plus WATCHED managers; recent data sets keep everyone.
The raw files are complete, so a different threshold is one rebuild away.
"""

import csv
import io
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import CusipMapping, InstitutionalFiler, InstitutionalPosition, Security

VALUE_IN_DOLLARS_FROM = date(2023, 1, 3)
FULL_SINCE = date(2025, 6, 1)  # data sets covering filings from here on: every filer
HISTORY_MIN_AUM = 1e9
COMMIT_ROWS = 50_000  # positions per transaction: short write locks for concurrent jobs
# Notable managers kept in history whatever their size (CIKs from their 13F filings).
WATCHED = {
    1067983,  # Berkshire Hathaway
    1336528,  # Pershing Square
    1649339,  # Scion Asset Management
    1061768,  # Baupost
    1656456,  # Appaloosa
    1079114,  # Greenlight Capital
    1040273,  # Third Point
    921669,  # Carl Icahn
    1709323,  # Himalaya Capital
    1536411,  # Duquesne Family Office
    1029160,  # Soros Fund Management
    1167483,  # Tiger Global
    1061165,  # Lone Pine
    1418814,  # ValueAct
    1791786,  # Elliott
    1112520,  # Akre
    1056831,  # Fairholme
    1115373,  # Semper Augustus
    1720792,  # Ruane, Cunniff & Goldfarb
    860643,  # Gardner Russo & Quinn
    1345471,  # Trian
    1998597,  # JANA Partners
    1517137,  # Starboard Value
    1096343,  # Markel
    949509,  # Oaktree
    1549575,  # Dalal Street (Mohnish Pabrai)
}


def _date(value: str | None) -> date | None:
    try:
        return datetime.strptime(value.strip(), "%d-%b-%Y").date() if value else None
    except ValueError:
        return None


def _num(value: str | None) -> float:
    try:
        return float(value) if value else 0.0
    except ValueError:
        return 0.0


@dataclass
class Filing:
    accession: str
    cik: int
    period: date
    filed: date | None
    replaces: bool  # original report or restatement: replaces the quarter's positions
    name: str | None


def parse_dataset(data: bytes) -> tuple[list[Filing], dict[str, list[dict[str, Any]]]]:
    """Filings with holdings, and their positions summed per (CUSIP, option type)."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        # Some files keep their tables in a folder ("01JUN2025-31AUG2025_form13f/...").
        members = {n.rsplit("/", 1)[-1]: n for n in archive.namelist()}

        def rows(name: str):
            with archive.open(members[name]) as f:
                text = io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline="")
                yield from csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)

        covers = {r["ACCESSION_NUMBER"]: r for r in rows("COVERPAGE.tsv")}
        filings = {}
        for r in rows("SUBMISSION.tsv"):
            kind = r.get("SUBMISSIONTYPE", "")
            if not kind.startswith("13F-HR"):
                continue  # notices (13F-NT) carry no holdings
            cover = covers.get(r["ACCESSION_NUMBER"], {})
            period = _date(r.get("PERIODOFREPORT"))
            if period is None:
                continue
            amendment = (cover.get("AMENDMENTTYPE") or "").upper()
            filings[r["ACCESSION_NUMBER"]] = Filing(
                accession=r["ACCESSION_NUMBER"],
                cik=int(r["CIK"]),
                period=period,
                filed=_date(r.get("FILING_DATE")),
                replaces=kind == "13F-HR" or "RESTATEMENT" in amendment,
                name=cover.get("FILINGMANAGER_NAME"),
            )
        positions: dict[str, dict[tuple, dict[str, Any]]] = defaultdict(dict)
        for r in rows("INFOTABLE.tsv"):
            accession = r["ACCESSION_NUMBER"]
            if accession not in filings:
                continue
            cusip = (r.get("CUSIP") or "").strip().upper()
            if len(cusip) != 9:
                continue
            put_call = (r.get("PUTCALL") or "").strip().upper()
            key = (cusip, put_call)
            entry = positions[accession].get(key)
            if entry is None:
                positions[accession][key] = entry = {
                    "cusip": cusip,
                    "put_call": put_call,
                    "issuer_name": r.get("NAMEOFISSUER"),
                    "title": r.get("TITLEOFCLASS"),
                    "share_type": (r.get("SSHPRNAMTTYPE") or "").strip() or None,
                    "shares": 0.0,
                    "value": 0.0,
                    "figi": (r.get("FIGI") or "").strip() or None,
                }
            entry["shares"] += _num(r.get("SSHPRNAMT"))
            entry["value"] += _num(r.get("VALUE"))
    ordered = sorted(filings.values(), key=lambda f: (f.filed or date.min, f.accession))
    for f in ordered:
        if f.filed and f.filed < VALUE_IN_DOLLARS_FROM:  # reported in thousands then
            for p in positions.get(f.accession, {}).values():
                p["value"] *= 1000
    return ordered, {a: list(p.values()) for a, p in positions.items()}


def load(session: Session, data: bytes, min_aum: float | None = None) -> int:
    """Load one data set; returns positions written. With `min_aum`, reports of smaller
    filers (outside WATCHED) are skipped entirely."""
    filings, positions = parse_dataset(data)
    if min_aum is not None:
        loaded = set(
            session.execute(
                select(InstitutionalPosition.filer_cik, InstitutionalPosition.period).distinct()
            ).all()
        )
        kept: set[tuple[int, date]] = set()
        selected = []
        for f in filings:  # in filing order: originals before their amendments
            big = sum(p["value"] for p in positions.get(f.accession, [])) >= min_aum
            if f.replaces and (f.cik in WATCHED or big):
                kept.add((f.cik, f.period))
            if (f.cik, f.period) in kept or (not f.replaces and (f.cik, f.period) in loaded):
                selected.append(f)  # amendments follow their original report
        filings = selected
    upsert(
        session,
        InstitutionalFiler,
        [{"cik": f.cik, "name": f.name} for f in filings if f.name],
        key=["cik"],
    )
    figis: dict[str, str] = {}
    written = pending = 0
    for f in filings:
        rows = positions.get(f.accession, [])
        if f.replaces:
            session.execute(
                delete(InstitutionalPosition).where(
                    InstitutionalPosition.filer_cik == f.cik,
                    InstitutionalPosition.period == f.period,
                )
            )
        records = []
        for p in rows:
            if p["figi"]:
                figis.setdefault(p["cusip"], p["figi"])
            records.append(
                {k: v for k, v in p.items() if k != "figi"}
                | {
                    "filer_cik": f.cik,
                    "period": f.period,
                    "accession": f.accession,
                    "filed": f.filed,
                }
            )
        count = upsert(
            session,
            InstitutionalPosition,
            records,
            key=["filer_cik", "period", "cusip", "put_call"],
        )
        written, pending = written + count, pending + count
        if pending >= COMMIT_ROWS:
            session.commit()  # a data set is millions of rows: don't hold the write lock
            pending = 0
    # FIGIs some filers report: free mappings, no OpenFIGI call needed.
    upsert(
        session,
        CusipMapping,
        [{"cusip": c, "figi": f} for c, f in figis.items()],
        key=["cusip"],
        update=[],
    )
    return written


def link_securities(session: Session) -> int:
    """Attach securities to CUSIP mappings by composite FIGI, FIGI or ticker."""
    securities = list(session.scalars(select(Security)))
    by_figi = {s.figi: s.id for s in securities if s.figi}
    by_class = {s.share_class_figi: s.id for s in securities if s.share_class_figi}
    by_ticker = {s.ticker: s.id for s in securities if s.ticker}
    linked = 0
    for m in session.scalars(select(CusipMapping)):
        security_id = (
            by_figi.get(m.composite_figi or "")
            or by_figi.get(m.figi or "")
            or by_class.get(m.figi or "")
            or by_ticker.get((m.ticker or "").replace("/", "-"))
        )
        if security_id and m.security_id != security_id:
            m.security_id = security_id
            linked += 1
    return linked


def unmapped_cusips(session: Session) -> list[str]:
    """CUSIPs held in positions that aren't linked to a security and haven't been looked up
    on OpenFIGI yet. A FIGI supplied by the filer may be an exchange-level one rather than
    the composite FIGI our securities carry, so it doesn't count as resolved by itself."""
    resolved = select(CusipMapping.cusip).where(
        CusipMapping.security_id.is_not(None) | CusipMapping.security_type.is_not(None)
    )
    return list(
        session.scalars(
            select(InstitutionalPosition.cusip)
            .distinct()
            .where(InstitutionalPosition.cusip.not_in(resolved))
        )
    )


def load_openfigi(session: Session, cusips: list[str], results: list[dict[str, Any]]) -> int:
    """Store OpenFIGI's answers (one per requested CUSIP, in order). `security_type` is set
    either way, marking the CUSIP as looked up ("unknown" when OpenFIGI has no match)."""
    records = []
    for cusip, result in zip(cusips, results, strict=False):
        data = (result or {}).get("data") or []
        # Prefer the US composite listing when OpenFIGI returns several.
        best = next((d for d in data if d.get("exchCode") == "US"), data[0] if data else None)
        records.append(
            {
                "cusip": cusip,
                "figi": best.get("figi") if best else None,
                "composite_figi": best.get("compositeFIGI") if best else None,
                "ticker": best.get("ticker") if best else None,
                "security_type": (best.get("securityType") or "other") if best else "unknown",
            }
        )
    return upsert(session, CusipMapping, records, key=["cusip"])


# --- views -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Change:
    cusip: str
    put_call: str
    issuer_name: str | None
    ticker: str | None
    shares_before: float
    shares_after: float
    value_after: float
    change: str  # new, added, reduced, sold, unchanged


def manager_changes(
    session: Session, filer_cik: int, period: date | None = None
) -> tuple[date | None, list[Change]]:
    """A manager's positions at a quarter end vs the previous quarter it reported."""
    periods = sorted(
        session.scalars(
            select(InstitutionalPosition.period)
            .distinct()
            .where(InstitutionalPosition.filer_cik == filer_cik)
        ),
        reverse=True,
    )
    if not periods:
        return None, []
    current = period or periods[0]
    earlier = [p for p in periods if p < current]
    previous = earlier[0] if earlier else None

    def holdings(p: date | None) -> dict[tuple, InstitutionalPosition]:
        if p is None:
            return {}
        rows = session.scalars(
            select(InstitutionalPosition).where(
                InstitutionalPosition.filer_cik == filer_cik, InstitutionalPosition.period == p
            )
        )
        return {(r.cusip, r.put_call): r for r in rows}

    now, before = holdings(current), holdings(previous)
    tickers = _tickers(session, {c for c, _ in now} | {c for c, _ in before})
    out = []
    for key in now.keys() | before.keys():
        a, b = before.get(key), now.get(key)
        s0, s1 = (a.shares or 0.0) if a else 0.0, (b.shares or 0.0) if b else 0.0
        if a is None:
            change = "new"
        elif b is None:
            change = "sold"
        elif s1 > s0:
            change = "added"
        elif s1 < s0:
            change = "reduced"
        else:
            change = "unchanged"
        ref = b or a
        assert ref is not None  # the key came from one of the two quarters
        out.append(
            Change(
                key[0],
                key[1],
                ref.issuer_name,
                tickers.get(key[0]),
                s0,
                s1,
                (b.value or 0.0) if b else 0.0,
                change,
            )
        )
    out.sort(key=lambda c: -c.value_after)
    return current, out


def _tickers(session: Session, cusips: set[str]) -> dict[str, str]:
    rows = session.execute(
        select(CusipMapping.cusip, Security.ticker)
        .join(Security, Security.id == CusipMapping.security_id)
        .where(CusipMapping.cusip.in_(cusips))
    )
    return {c: t for c, t in rows if t}


def find_filers(session: Session, query: str, limit: int = 20) -> list[InstitutionalFiler]:
    return list(
        session.scalars(
            select(InstitutionalFiler)
            .where(func.lower(InstitutionalFiler.name).contains(query.lower()))
            .order_by(InstitutionalFiler.name)
            .limit(limit)
        )
    )
