"""Economic release calendar: when each macro series is next published (FRED releases).

Market-moving releases (CPI, payrolls, GDP, FOMC decisions) are scheduled; knowing the next
dates helps avoid opening positions into an event, or plan around one.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel.db import upsert
from fin_intel.models import EconomicRelease, EconomicReleaseDate, EconomicSeries

# FOMC rate decisions aren't a FRED release; they come from the Fed's calendar page and
# are stored as a release of our own (an ID outside FRED's range).
FOMC_RELEASE_ID = 900_001
FOMC_LINK = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
BOJ_RELEASE_ID = 900_002
BOJ_LINK = "https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm"
# Release dates are kept from here on (each refresh re-reads them all: a release's dates can
# move), so research can line signals up with past CPI, payrolls and FOMC days.
HISTORY_START = date(2000, 1, 1)


def load_series_release(session: Session, series_id: str, payload: dict[str, Any]) -> int:
    releases = payload.get("releases") or []
    if not releases:
        return 0
    r = releases[0]
    upsert(
        session,
        EconomicRelease,
        [{"id": r["id"], "name": r.get("name"), "link": r.get("link")}],
        key=["id"],
    )
    series = session.get(EconomicSeries, series_id)
    if series is not None:
        series.release_id = r["id"]
    return 1


def load_release_dates(session: Session, release_id: int, payload: dict[str, Any]) -> int:
    """A release's dates, replacing those loaded before in the same span (a schedule can
    move, e.g. during a government shutdown)."""
    dates = sorted({date.fromisoformat(d["date"]) for d in payload.get("release_dates") or []})
    if session.get(EconomicRelease, release_id) is None:
        session.add(EconomicRelease(id=release_id))
        session.flush()
    return _replace_dates(session, release_id, dates)


def _replace_dates(session: Session, release_id: int, dates: list[date]) -> int:
    if dates:
        session.execute(
            delete(EconomicReleaseDate).where(
                EconomicReleaseDate.release_id == release_id,
                EconomicReleaseDate.date >= dates[0],
            )
        )
    return upsert(
        session,
        EconomicReleaseDate,
        [{"release_id": release_id, "date": d} for d in dates],
        key=["release_id", "date"],
    )


_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}


def parse_fomc(page: bytes) -> list[date]:
    """Decision days (each meeting's last day) from the Fed's FOMC calendar page."""
    text = page.decode("utf-8", errors="replace")
    out = set()
    sections = re.split(r"(\d{4}) FOMC Meetings", text)
    for year, body in zip(sections[1::2], sections[2::2], strict=False):
        for month, days in re.findall(
            r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?"
            r"fomc-meeting__date[^>]*>([^<]+)<",
            body,
            re.S,
        ):
            last_month = month.split("/")[-1].strip()[:3].lower()
            day = re.findall(r"\d+", days)
            if last_month not in _MONTHS or not day or "notation" in days.lower():
                continue
            out.add(date(int(year), _MONTHS[last_month], int(day[-1])))
    return sorted(out)


def load_fomc(session: Session, page: bytes) -> int:
    upsert(
        session,
        EconomicRelease,
        [{"id": FOMC_RELEASE_ID, "name": "FOMC rate decision", "link": FOMC_LINK}],
        key=["id"],
    )
    return _replace_dates(session, FOMC_RELEASE_ID, parse_fomc(page))


def parse_boj(page: bytes) -> list[date]:
    """Decision days (each meeting's last day, extraordinary meetings included) from a Bank
    of Japan schedule page: one table per year, the meeting dates in each row's first cell
    ("Jan. 22 (Thurs.), 23 (Fri.)", "Oct. 31 (Mon.), Nov. 1 (Tues.)")."""
    text = page.decode("utf-8", errors="replace")
    out = set()
    sections = re.split(r"Table : (\d{4})", text)
    for year, body in zip(sections[1::2], sections[2::2], strict=False):
        table = body.split("</table>")[0]
        for cell in re.findall(r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>", table, re.S):
            cell = html.unescape(re.sub(r"<[^>]+>|\[.*?\]|\(.*?\)", " ", cell))
            explicit = re.search(r"\b(\d{4})\b", cell)
            month, day = None, None
            for name, number in re.findall(
                r"(?:\b([A-Z][a-z]{2})[a-z]*\.?\s*)?\b(\d{1,2})\b", cell
            ):
                if name:
                    month = _MONTHS.get(name.lower())
                day = int(number)
            if month and day:
                out.add(date(int(explicit.group(1)) if explicit else int(year), month, day))
    return sorted(out)


def load_boj(session: Session, page: bytes) -> int:
    """Replaces dates only within the years the page covers: the current and past pages
    each hold part of the history."""
    upsert(
        session,
        EconomicRelease,
        [{"id": BOJ_RELEASE_ID, "name": "Bank of Japan policy decision", "link": BOJ_LINK}],
        key=["id"],
    )
    dates = parse_boj(page)
    if dates:
        session.execute(
            delete(EconomicReleaseDate).where(
                EconomicReleaseDate.release_id == BOJ_RELEASE_ID,
                EconomicReleaseDate.date >= date(dates[0].year, 1, 1),
                EconomicReleaseDate.date <= date(dates[-1].year, 12, 31),
            )
        )
    return upsert(
        session,
        EconomicReleaseDate,
        [{"release_id": BOJ_RELEASE_ID, "date": d} for d in dates],
        key=["release_id", "date"],
    )


@dataclass
class Event:
    date: date
    release_id: int
    release: str | None
    series: list[str] = field(default_factory=list)  # macro series it updates


def upcoming(
    session: Session, days: int = 30, start: date | None = None, daily: bool = False
) -> list[Event]:
    """Releases scheduled in the window, with the tracked series each one updates. Releases
    with daily series (rates, spreads, VIX) are left out unless `daily`."""
    start = start or date.today()
    rows = session.execute(
        select(EconomicReleaseDate.date, EconomicRelease.id, EconomicRelease.name)
        .join(EconomicRelease, EconomicRelease.id == EconomicReleaseDate.release_id)
        .where(
            EconomicReleaseDate.date >= start,
            EconomicReleaseDate.date <= start + timedelta(days=days),
        )
        .order_by(EconomicReleaseDate.date, EconomicRelease.name)
    ).all()
    series: dict[int, list[str]] = {}
    frequencies: dict[int, set[str | None]] = {}
    for sid, rid, frequency in session.execute(
        select(EconomicSeries.id, EconomicSeries.release_id, EconomicSeries.frequency).where(
            EconomicSeries.release_id.is_not(None)
        )
    ):
        series.setdefault(rid, []).append(sid)
        frequencies.setdefault(rid, set()).add(frequency)
    return [
        Event(d, rid, name, sorted(series.get(rid, [])))
        for d, rid, name in rows
        if daily or "D" not in frequencies.get(rid, set())
    ]
