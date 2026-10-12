"""Conflict events, for the warfare thesis (docs/research/theses.md, thesis 6): where
fighting happens, by whom, how, and whether the preconditions for peace hold or retract.

- UCDP (Uppsala, CC BY 4.0, no account): organized violence with fatalities since 1989.
  The yearly Georeferenced Event Dataset is final; monthly candidate releases cover the
  current year with about a month's lag (global since October 2020) and give way to the
  yearly release once it covers them. Point in time (`ucdp_available_on`): a month after
  the event's month from October 2020, before that mid-year after the event's year.
- ACLED: also protests, riots and weapon detail (drone and air strikes are their own
  sub-event type; notes name the weapons). Needs a Research-tier account. ACLED publishes
  weekly and revises, so each sync re-reads the last few months; an event counts as
  public a week after it happened (`available_on`). Revisions aren't versioned.
"""

import calendar
import csv
import io
import logging
import zipfile
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import ConflictEvent, UcdpEvent
from fin_intel.pipeline import tracked
from fin_intel.providers import AcledProvider, UcdpProvider
from fin_intel.providers.acled import PAGE
from fin_intel.providers.errors import NotFoundError
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

START = date(2018, 1, 1)  # ACLED's coverage became global around 2018-2020
REVISE_MONTHS = 3  # months re-read on every sync: ACLED keeps revising recent events
PUBLICATION_LAG = timedelta(days=7)


def available_on(event_date: date) -> date:
    return event_date + PUBLICATION_LAG


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except TypeError, ValueError:
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except TypeError, ValueError:
        return None


def parse(payload: Any) -> list[dict[str, Any]]:
    rows = []
    for e in (payload or {}).get("data") or []:
        if not e.get("event_id_cnty") or not e.get("event_date"):
            continue
        stamp = _int(e.get("timestamp"))
        rows.append(
            {
                "id": e["event_id_cnty"],
                "event_date": date.fromisoformat(e["event_date"]),
                "disorder_type": e.get("disorder_type") or None,
                "event_type": e.get("event_type") or "",
                "sub_event_type": e.get("sub_event_type") or None,
                "actor1": e.get("actor1") or None,
                "assoc_actor_1": e.get("assoc_actor_1") or None,
                "actor2": e.get("actor2") or None,
                "assoc_actor_2": e.get("assoc_actor_2") or None,
                "interaction": str(e["interaction"]) if e.get("interaction") else None,
                "civilian_targeting": e.get("civilian_targeting") or None,
                "country": e.get("country") or "",
                "iso": _int(e.get("iso")),
                "region": e.get("region") or None,
                "admin1": e.get("admin1") or None,
                "location": e.get("location") or None,
                "latitude": _float(e.get("latitude")),
                "longitude": _float(e.get("longitude")),
                "fatalities": _int(e.get("fatalities")) or 0,
                "source": e.get("source") or None,
                "notes": e.get("notes") or None,
                "tags": e.get("tags") or None,
                "updated": datetime.fromtimestamp(stamp, UTC) if stamp else None,
            }
        )
    return rows


def load(session: Session, payload: Any) -> int:
    return upsert(session, ConflictEvent, parse(payload), key=["id"])


def months(today: date) -> list[date]:
    out, m = [], START
    while m <= today:
        out.append(m)
        m = date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    return out


def sync_month(session: Session, acled: AcledProvider, month: date) -> int:
    first = month
    last = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    with tracked(session, "acled", "events", f"{month:%Y-%m}") as result:
        session.commit()  # fetch-then-load
        pages, page = [], 1
        while True:
            payload = acled.fetch_month(f"{month:%Y-%m}", first.isoformat(), last.isoformat(), page)
            if page == 1 and payload.get("data_query_restrictions"):
                log.warning("acled restrictions: %s", payload["data_query_restrictions"])
            pages.append(payload)
            if len(payload.get("data") or []) < PAGE:
                break
            page += 1
        result["rows"] = sum(load(session, p) for p in pages)
    return result["rows"]


def wanted(session: Session, today: date) -> list[date]:
    """Months never synced, and the last REVISE_MONTHS again."""
    from fin_intel import pipeline

    done = pipeline.synced_since(session, "acled", "events", datetime(2000, 1, 1, tzinfo=UTC))
    all_months = months(today)
    recent = set(all_months[-REVISE_MONTHS:])
    return [m for m in all_months if m in recent or f"{m:%Y-%m}" not in done]


# --- UCDP ---

CANDIDATE_GLOBAL = date(2020, 10, 1)  # monthly candidate releases global from v20.0.10
YEARLY_RELEASE = (6, 30)  # the yearly dataset comes out around mid-year


def ucdp_available_on(event_end: date) -> date:
    if event_end >= CANDIDATE_GLOBAL:
        month_end = event_end.replace(day=calendar.monthrange(event_end.year, event_end.month)[1])
        return month_end + timedelta(days=31)
    return date(event_end.year + 1, *YEARLY_RELEASE)


def _day(text: str) -> date:
    return date.fromisoformat(text[:10])


def parse_ucdp(text: str, dataset: str) -> list[dict[str, Any]]:
    rows = []
    for e in csv.DictReader(io.StringIO(text)):
        if not (e.get("id") and e.get("date_start")):
            continue
        rows.append(
            {
                "dataset": dataset,
                "id": int(e["id"]),
                "relid": e.get("relid") or None,
                "year": int(e["year"]),
                "type_of_violence": int(e["type_of_violence"]),
                "conflict_name": e.get("conflict_name") or None,
                "dyad_name": e.get("dyad_name") or None,
                "side_a": e.get("side_a") or None,
                "side_b": e.get("side_b") or None,
                "country": e.get("country") or "",
                "country_id": _int(e.get("country_id")),
                "region": e.get("region") or None,
                "adm_1": e.get("adm_1") or None,
                "latitude": _float(e.get("latitude")),
                "longitude": _float(e.get("longitude")),
                "date_start": _day(e["date_start"]),
                "date_end": _day(e.get("date_end") or e["date_start"]),
                **{
                    k: _int(e.get(k)) or 0
                    for k in (
                        "deaths_a",
                        "deaths_b",
                        "deaths_civilians",
                        "deaths_unknown",
                        "best",
                        "low",
                        "high",
                    )
                },
                "source_headline": (e.get("source_headline") or "")[:1000] or None,
            }
        )
    return rows


def _final_year(session: Session) -> int:
    return session.scalar(select(func.max(UcdpEvent.year)).where(UcdpEvent.dataset == "ged")) or 0


def load_ged(session: Session, payload: bytes) -> int:
    """The yearly dataset replaces the previous one and the candidate events it covers."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        name = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        rows = parse_ucdp(archive.read(name).decode("utf-8-sig"), "ged")
    if not rows:
        return 0
    session.execute(delete(UcdpEvent).where(UcdpEvent.dataset == "ged"))
    last = max(r["year"] for r in rows)
    session.execute(
        delete(UcdpEvent).where(UcdpEvent.dataset == "candidate", UcdpEvent.year <= last)
    )
    return upsert(session, UcdpEvent, rows, key=["dataset", "id"])


def load_candidate(session: Session, payload: bytes) -> int:
    final = _final_year(session)
    rows = [r for r in parse_ucdp(payload.decode("utf-8-sig"), "candidate") if r["year"] > final]
    return upsert(session, UcdpEvent, rows, key=["dataset", "id"])


def ucdp_versions(today: date) -> tuple[list[str], list[str]]:
    """Yearly versions to try, newest first ("261", "251"), and the monthly candidate
    versions of the current year ("26_0_1"... up to the current month)."""
    yy = today.year % 100
    yearly = [f"{yy}1", f"{yy - 1}1"]
    monthly = [f"{yy}_0_{m}" for m in range(1, today.month + 1)]
    return yearly, monthly


def sync_ucdp(session: Session, ucdp: UcdpProvider, today: date) -> int:
    """The newest yearly dataset (once per version) and every monthly candidate release of
    the current year not stored yet; releases not out yet answer 404."""
    with tracked(session, "ucdp", "events", "all") as result:
        session.commit()  # fetch-then-load
        store = ucdp.raw_store
        have_ged = set(store.latest_hashes("ucdp", "ged")) if store else set()
        have_cand = set(store.latest_hashes("ucdp", "candidate")) if store else set()
        yearly, monthly = ucdp_versions(today)
        rows = 0
        for version in yearly:
            if version in have_ged:
                break
            try:
                payload = ucdp.fetch_ged(version)
            except NotFoundError:
                continue
            rows += load_ged(session, payload)
            session.commit()
            break
        for version in monthly:
            if version in have_cand:
                continue
            try:
                payload = ucdp.fetch_candidate(version)
            except NotFoundError:
                break
            rows += load_candidate(session, payload)
            session.commit()
        result["rows"] = rows
    return result["rows"]


registry.register(
    Dataset("acled", "events", lambda s, k, p, t: load(s, p), ("conflict",)),
    Dataset("ucdp", "ged", lambda s, k, p, t: load_ged(s, p), ("conflict",), payload="bytes"),
    Dataset(
        "ucdp",
        "candidate",
        lambda s, k, p, t: load_candidate(s, p),
        ("conflict",),
        payload="bytes",
    ),
)
