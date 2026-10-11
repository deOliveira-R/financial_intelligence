"""SEC EDGAR filings: company facts (bulk and per company), submissions (profiles and
corporate events), and filings missing from company facts (read from their XBRL)."""

import logging
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel import derive, filing_events, registry, world, xbrl
from fin_intel.db import upsert
from fin_intel.models import Concept, Fact, Filing, Issuer
from fin_intel.pipeline import tracked, utc_today
from fin_intel.providers import ProviderError, SecProvider, sec
from fin_intel.raw import RawStore
from fin_intel.registry import Dataset
from fin_intel.symbols import get_security

log = logging.getLogger(__name__)


def load_filing_xbrl(session: Session, key: str, body: bytes) -> int:
    """Facts from one filing's XBRL instance (where SEC's company facts lack them)."""
    cik, accession, filed, form = key.split("|")
    payload = xbrl.parse_instance(body, accession, form, date.fromisoformat(filed))
    if not payload["facts"]:
        return 0
    return load_company_facts(session, int(cik), payload, supplement=True)


def load_submissions(session: Session, cik: int, payload: Any) -> int:
    """A filer's SIC code and category (SEC submissions)."""
    issuer = session.get(Issuer, cik)
    if issuer is None:
        return 0
    sic = str(payload.get("sic") or "").strip()
    issuer.sic = int(sic) if sic.isdigit() else None
    issuer.sic_description = payload.get("sicDescription") or None
    issuer.filer_category = payload.get("category") or ""  # "" marks it looked up
    issuer.country = world.sec_country(payload) or issuer.country
    recent = (payload.get("filings") or {}).get("recent") or {}
    filing_events.load(session, filing_events.parse(recent, cik))
    return 1


def load_submissions_page(session: Session, key: str, payload: Any) -> int:
    """Events from an older page of a filer's submissions. Key: `cik|page name`."""
    cik = int(key.split("|")[0])
    if session.get(Issuer, cik) is None:
        return 0
    return filing_events.load(session, filing_events.parse(payload or {}, cik))


def sync_events(session: Session, sec_provider: SecProvider, cik: int) -> int:
    """A filer's events: its current submissions (recent filings, refreshed each run) and
    every older page not stored yet."""
    with tracked(session, "sec", "events", str(cik)) as result:
        session.commit()  # fetch-then-load
        payload = sec_provider.fetch_submissions(cik)
        store = sec_provider.raw_store
        stored = set(store.latest_hashes("sec", "submissions_page")) if store else set()
        pages = [
            f["name"]
            for f in (payload.get("filings") or {}).get("files") or []
            if f.get("name") and f"{cik}|{f['name']}" not in stored
        ]
        older = [(name, sec_provider.fetch_submissions_page(cik, name)) for name in pages]
        rows = load_submissions(session, cik, payload)
        for name, page in older:
            rows += load_submissions_page(session, f"{cik}|{name}", page)
        result["rows"] = rows
    return result["rows"]


def load_company_facts(session: Session, cik: int, payload: Any, supplement: bool = False) -> int:
    """SEC company facts for one filer. With `supplement` (facts read from one filing's
    XBRL), names and concept labels already known aren't overwritten."""
    filings, concepts, facts = sec.parse_company_facts(cik, payload)
    keep = [] if supplement else None
    upsert(
        session, Issuer, [{"cik": cik, "name": payload.get("entityName")}], key=["cik"], update=keep
    )
    upsert(session, Filing, filings, key=["accession"])
    upsert(session, Concept, concepts, key=["taxonomy", "name"], update=keep)

    filing_ids = dict(
        session.execute(select(Filing.accession, Filing.id).where(Filing.cik == cik)).all()
    )
    concept_ids = {
        (t, n): i for t, n, i in session.execute(select(Concept.taxonomy, Concept.name, Concept.id))
    }
    rows = [
        {
            "filing_id": filing_ids[f["accession"]],
            "concept_id": concept_ids[f["concept"]],
            "unit": f["unit"],
            "period_start": f["period_start"],
            "period_end": f["period_end"],
            "instant": f["instant"],
            "value": f["value"],
            "frame": f["frame"],
            "cik": cik,
        }
        for f in facts
    ]
    count = upsert(
        session,
        Fact,
        rows,
        key=["filing_id", "concept_id", "unit", "period_start", "period_end"],
    )
    derive.derive_issuer(session, cik)
    return count


def sync_fundamentals(session: Session, sec_provider: SecProvider, ticker: str) -> int:
    security = get_security(session, ticker)
    if security is None or security.cik is None:
        raise ProviderError(f"{ticker}: no CIK known; run sync-tickers first")
    with tracked(session, "sec", "companyfacts", str(security.cik)) as result:
        payload = sec_provider.fetch_company_facts(security.cik)
        result["rows"] = load_company_facts(session, security.cik, payload)
    return result["rows"]


def load_bulk_company_facts(
    session: Session,
    store: RawStore,
    zip_path: Path,
    ciks: set[int] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int]:
    """Load SEC's bulk companyfacts.zip, one company at a time, skipping unchanged ones.

    Each entry is stored as its own raw response (dataset companyfacts, key = CIK), exactly
    like a per-company fetch, so rebuilds and retention treat both the same. Entries whose
    content matches the latest stored response for that company are skipped: on a normal
    night only companies that filed something are reloaded. Returns (loaded, unchanged).
    """
    import hashlib
    import json
    import zipfile

    latest = store.latest_hashes("sec", "companyfacts")
    loaded = unchanged = 0
    with zipfile.ZipFile(zip_path) as archive:
        entries = [
            (int(info.filename[3:13]), info)
            for info in archive.infolist()
            if info.filename.startswith("CIK") and info.filename.endswith(".json")
        ]
        entries = [(cik, info) for cik, info in entries if ciks is None or cik in ciks]
        for n, (cik, info) in enumerate(entries, start=1):
            body = archive.read(info)
            if latest.get(str(cik)) == hashlib.sha256(body).hexdigest():
                unchanged += 1
            else:
                # Fetch-then-load per company: commit first so the raw store's own
                # transaction never waits on this session's write lock.
                session.commit()
                store.save("sec", "companyfacts", str(cik), None, 200, body)
                load_company_facts(session, cik, json.loads(body))
                session.commit()
                loaded += 1
            if progress:
                progress(n, len(entries))
    return loaded, unchanged


THIN_FILING = 50  # a periodic report with fewer facts than this lacks its financials

QUARTERLY_STALE, ANNUAL_STALE = timedelta(days=200), timedelta(days=430)


def stale_issuers(session: Session) -> list[int]:
    """Issuers of primary listings whose latest financials are older than their filing
    rhythm allows (a quarter for 10-Q filers, a year for 20-F filers): a periodic report
    is probably missing from SEC's company facts."""
    from fin_intel import metrics
    from fin_intel.models import StatementItem

    ciks = list(metrics.primary_securities(session))
    latest = dict(
        session.execute(
            select(StatementItem.cik, func.max(StatementItem.period_end))
            .where(
                StatementItem.cik.in_(ciks),
                StatementItem.line_item.in_(("revenue", "net_income", "total_assets")),
            )
            .group_by(StatementItem.cik)
        ).all()
    )
    quarterly = set(
        session.scalars(
            select(Filing.cik)
            .where(Filing.form == "10-Q", Filing.filed >= utc_today() - timedelta(days=730))
            .distinct()
        )
    )
    today = utc_today()
    return sorted(
        cik
        for cik in ciks
        if latest.get(cik) is None
        or today - latest[cik] > (QUARTERLY_STALE if cik in quarterly else ANNUAL_STALE)
    )


def sync_xbrl_gaps(session: Session, sec_provider: SecProvider, cik: int) -> int:
    """Read the XBRL of an issuer's recent periodic reports that SEC's company facts are
    missing (or nearly empty for), straight from each filing."""
    with tracked(session, "sec", "xbrl_gaps", str(cik)) as result:
        session.commit()  # fetch-then-load
        submissions = sec_provider.fetch_submissions(cik)
        load_submissions(session, cik, submissions)
        recent = (submissions.get("filings") or {}).get("recent") or {}
        since = (utc_today() - timedelta(days=730)).isoformat()
        fact_counts = dict(
            session.execute(
                select(Filing.accession, func.count(Fact.filing_id))
                .outerjoin(Fact, Fact.filing_id == Filing.id)
                .where(Filing.cik == cik)
                .group_by(Filing.accession)
            ).all()
        )
        store = sec_provider.raw_store
        read = (
            {key.split("|")[1] for key in store.latest_hashes("sec", "filing_xbrl") if key}
            if store
            else set()
        )
        rows = 0
        for accession, form, filed, is_xbrl in zip(
            recent.get("accessionNumber", []),
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("isXBRL", []),
            strict=False,
        ):
            if form not in xbrl.PERIODIC_FORMS or not is_xbrl or filed < since:
                continue
            if fact_counts.get(accession, 0) >= THIN_FILING or accession in read:
                continue
            session.commit()
            index = sec_provider.fetch_filing_index(cik, accession)
            names = [i["name"] for i in (index.get("directory") or {}).get("item", [])]
            name = xbrl.instance_file(names)
            if name is None:
                continue
            body = sec_provider.fetch_xbrl_instance(
                cik, accession, name, form, date.fromisoformat(filed)
            )
            rows += load_filing_xbrl(session, f"{cik}|{accession}|{filed}|{form}", body)
        result["rows"] = rows
    return result["rows"]


def issuers_without_sic(session: Session) -> list[int]:
    """Issuers of primary listings whose SEC profile hasn't been looked up yet."""
    from fin_intel import metrics

    looked_up = set(session.scalars(select(Issuer.cik).where(Issuer.filer_category.is_not(None))))
    return sorted(cik for cik in metrics.primary_securities(session) if cik not in looked_up)


def sync_submissions(session: Session, sec_provider: SecProvider, cik: int) -> int:
    with tracked(session, "sec", "submissions", str(cik)) as result:
        session.commit()  # fetch-then-load
        result["rows"] = load_submissions(session, cik, sec_provider.fetch_submissions(cik))
    return result["rows"]


registry.register(
    Dataset(
        "sec",
        "companyfacts",
        lambda s, k, p, t: load_company_facts(s, int(k), p),
        ("fundamentals",),
        snapshot=True,
    ),
    Dataset(
        "sec",
        "submissions",
        lambda s, k, p, t: load_submissions(s, int(k), p),
        ("fundamentals", "events"),
        snapshot=True,
    ),
    Dataset(
        "sec", "submissions_page", lambda s, k, p, t: load_submissions_page(s, k, p), ("events",)
    ),
    Dataset(
        "sec",
        "filing_xbrl",
        lambda s, k, p, t: load_filing_xbrl(s, k, p),
        ("fundamentals",),
        payload="bytes",
    ),
)
