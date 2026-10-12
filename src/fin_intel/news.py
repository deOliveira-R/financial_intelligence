"""Worldwide news attention to the research theses (GDELT): daily counts of online news
articles in 65 languages matching curated queries, since 2017, with each day's total of
monitored articles so a series reads as a share of all coverage (GDELT's own volume
grows and dips).

Attention isn't the event: it measures what the world's press is talking about, which is
the point for thesis trackers (docs/research/theses.md): drone warfare and counter-drone
systems, ceasefires and mobilizations (the preconditions for peace), sovereign-bond
stress, private credit, AI layoffs and humanoid robots. Each query's text is part of its
raw key, so editing a query refetches its history instead of mixing two definitions.

Point in time: a day's articles are known the next day.
"""

import hashlib
import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel import indicators, registry, timeseries
from fin_intel.db import upsert
from fin_intel.models import NewsVolume
from fin_intel.pipeline import tracked
from fin_intel.providers import GdeltProvider
from fin_intel.registry import Dataset

log = logging.getLogger(__name__)

FIRST_YEAR = 2017  # the DOC API's timelines start in 2017

# name -> (thesis, GDELT query: "phrases", OR inside parentheses)
QUERIES: dict[str, tuple[str, str]] = {
    "drone_warfare": (
        "warfare",
        '("drone strike" OR "drone attack" OR "kamikaze drone" OR "loitering munition")',
    ),
    "counter_drone": ("warfare", '("counter-drone" OR "anti-drone" OR "drone interceptor")'),
    "ceasefire": ("peace", '(ceasefire OR "peace talks" OR "peace agreement")'),
    "mobilization": ("peace", '(mobilization OR conscription OR "martial law")'),
    "sanctions": ("peace", '("new sanctions" OR "sanctions package" OR "export controls")'),
    "trade_war": ("peace", '("trade war" OR "retaliatory tariffs")'),
    "bond_stress": (
        "sovereign",
        '("bond vigilantes" OR "fiscal dominance" OR "weak auction" OR "failed auction")',
    ),
    "yield_curve_control": ("sovereign", '"yield curve control"'),
    "dedollarization": ("sovereign", '(dedollarization OR "de-dollarization")'),
    "private_credit": ("credit", '"private credit"'),
    "credit_stress": ("credit", '("credit crunch" OR "bank run" OR "loan defaults")'),
    "layoffs": ("labor", '(layoffs OR "job cuts")'),
    "ai_bubble": ("ai", '"AI bubble"'),
    "ai_jobs": ("labor", '("replaced by AI" OR "AI layoffs" OR "jobs lost to AI")'),
    "entry_level_jobs": ("labor", '("entry-level jobs" OR "graduate unemployment")'),
    "humanoid_robot": ("robots", '"humanoid robot"'),
    "steel_demand": ("industry", '("steel demand" OR "steel output" OR "steel production")'),
}


def query_key(name: str, year: int) -> str:
    digest = hashlib.sha1(QUERIES[name][1].encode()).hexdigest()[:8]
    return f"{name}:{digest}|{year}"


def parse(payload: Any) -> list[tuple[date, int, int | None]]:
    out = []
    for series in (payload or {}).get("timeline") or []:
        for point in series.get("data") or []:
            day = datetime.strptime(point["date"][:8], "%Y%m%d").date()
            out.append((day, int(point.get("value") or 0), point.get("norm")))
    return out


def load(session: Session, key: str, payload: Any) -> int:
    """One query-year. A key whose query text no longer matches news.py is stale (the
    query was edited) and is skipped."""
    name = key.split(":", 1)[0]
    year = int(key.rsplit("|", 1)[1])
    if name not in QUERIES or query_key(name, year) != key:
        return 0
    rows = [
        {"query": name, "date": d, "articles": n, "total": t}
        for d, n, t in parse(payload)
        if d.year == year
    ]
    return upsert(session, NewsVolume, rows, key=["query", "date"])


def sync_query(session: Session, gdelt: GdeltProvider, name: str, today: date) -> int:
    """Every past year once (per query text), the current year on every run."""
    with tracked(session, "gdelt", "doc_timeline", name) as result:
        session.commit()  # fetch-then-load
        store = gdelt.raw_store
        have = set(store.latest_hashes("gdelt", "doc_timeline")) if store else set()
        rows = 0
        for year in range(FIRST_YEAR, today.year + 1):
            key = query_key(name, year)
            if key in have and year < today.year:
                continue
            end = min(date(year, 12, 31), today)
            payload = gdelt.fetch_timeline(
                key, QUERIES[name][1], f"{year}0101000000", f"{end:%Y%m%d}235959"
            )
            rows += load(session, key, payload)
            session.commit()
        result["rows"] = rows
    return result["rows"]


def drop_unknown(session: Session) -> int:
    """Rows of queries removed from QUERIES."""
    return session.execute(delete(NewsVolume).where(NewsVolume.query.not_in(QUERIES))).rowcount


@timeseries.source("news", lowercase=True)
def _spec(session: Session, ident: str, days: list[date], pit: bool) -> indicators.Series:
    """`news:drone_warfare`: articles per million monitored that day (`news:NAME:count`
    for the raw count), known the next day."""
    name, _, field = ident.partition(":")
    if name not in QUERIES:
        raise timeseries.SpecError(f"news:{name}: one of {', '.join(QUERIES)}")
    if field not in ("", "count"):
        raise timeseries.SpecError(f"news:{ident}: field is empty (share) or count")
    rows = session.execute(
        select(NewsVolume.date, NewsVolume.articles, NewsVolume.total)
        .where(NewsVolume.query == name)
        .order_by(NewsVolume.date)
    ).all()
    if not rows:
        raise timeseries.SpecError(f"no news data for {name}; run fin-intel sync-news")
    lag = timedelta(days=1) if pit else timedelta(0)
    known = [(d + lag, float(n) if field else (n / t * 1e6 if t else None)) for d, n, t in rows]
    return timeseries.step(known, days)


registry.register(
    Dataset("gdelt", "doc_timeline", lambda s, k, p, t: load(s, k, p), ("news",)),
)
