"""Conflict events (ACLED): political violence and protest worldwide, for the warfare
thesis (docs/research/theses.md, thesis 6): where fighting happens, by whom, how (drone
and air strikes are their own sub-event type, and the notes name the weapons), and
whether the preconditions for peace hold or retract.

ACLED publishes weekly and revises events afterwards, so each sync re-reads the last few
months and reads older months once. Point in time: an event counts as public a week after
it happened (`available_on`), ACLED's usual lag; revisions aren't versioned.
"""

import calendar
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from fin_intel import registry
from fin_intel.db import upsert
from fin_intel.models import ConflictEvent
from fin_intel.pipeline import tracked
from fin_intel.providers import AcledProvider
from fin_intel.providers.acled import PAGE
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


registry.register(
    Dataset("acled", "events", lambda s, k, p, t: load(s, p), ("conflict",)),
)
