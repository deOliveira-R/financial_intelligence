"""Screen the market on company metrics (metrics.py).

Filters are `metric<op>value` strings, e.g. "pe<15", "roic>=0.15", "market_cap>1e9",
"operating_margin_vs_5y<0.1". Sorting takes a metric name, "-" for descending. Ranking
"magic" orders by the sum of earnings-yield and ROIC ranks (Greenblatt's magic formula).

Presets bundle sensible filters. Each excludes companies whose operating margin is far
above its 5-year average: a cyclical at peak earnings looks cheapest right before its
earnings fall (shipping, commodities). Sectors (sectors.py, from SIC codes) can be
included or excluded; the magic formula leaves out financials and utilities, as
Greenblatt does.
"""

import operator
import re
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from fin_intel import sectors
from fin_intel.models import CompanyMetrics, Issuer, Security

NUMERIC = [
    c.name
    for c in CompanyMetrics.__table__.columns
    if c.name not in ("security_id", "as_of", "cik", "period_end", "currency")
]
OPS = {"<=": operator.le, ">=": operator.ge, "<": operator.lt, ">": operator.gt, "=": operator.eq}
_FILTER = re.compile(r"^\s*([a-z_0-9]+)\s*(<=|>=|<|>|=)\s*(-?[0-9.]+(?:e-?[0-9]+)?)\s*$")

NOT_AT_PEAK = "operating_margin_vs_5y<0.1"
PRESETS: dict[str, dict] = {
    "magic": {
        "filters": ["market_cap>=1e9", "earnings_yield>0", "roic>0", NOT_AT_PEAK],
        "rank": "magic",
        "exclude_sectors": ["finance", "utilities"],
    },
    "deep_value": {
        "filters": [
            "p_b<1",
            "piotroski_f>=7",
            "current_ratio>=1.5",
            "market_cap>=3e8",
            NOT_AT_PEAK,
        ],
        "sort": "p_b",
    },
    "quality": {
        "filters": [
            "roic>=0.15",
            "operating_margin>=0.15",
            "debt_to_equity<=1",
            "revenue_growth>=0.05",
            "market_cap>=1e9",
            NOT_AT_PEAK,
        ],
        "sort": "-roic",
    },
    "cash_cows": {
        "filters": [
            "fcf_yield>=0.08",
            "shareholder_yield>=0.04",
            "interest_coverage>=5",
            "market_cap>=1e9",
            NOT_AT_PEAK,
        ],
        "sort": "-fcf_yield",
        "exclude_sectors": ["finance"],
    },
}


class ScreenError(ValueError):
    pass


@dataclass(frozen=True)
class Filter:
    metric: str
    op: str
    value: float


def parse_filter(text: str) -> Filter:
    m = _FILTER.match(text)
    if not m:
        raise ScreenError(f"{text!r}: expected metric<op>value, e.g. pe<15")
    metric, op, value = m.groups()
    if metric not in NUMERIC:
        raise ScreenError(f"unknown metric {metric!r}; one of {', '.join(NUMERIC)}")
    return Filter(metric, op, float(value))


def screen(
    session: Session,
    filters: list[str] | None = None,
    sort: str | None = None,
    rank: str | None = None,
    preset: str | None = None,
    limit: int = 50,
    sector: list[str] | None = None,
    exclude_sectors: list[str] | None = None,
    as_of: date | None = None,
) -> tuple[date | None, list[dict]]:
    """Latest metrics matching every filter, sorted or ranked. Returns (as_of, rows)."""
    filters = list(filters or [])
    exclude = list(exclude_sectors or [])
    if preset:
        if preset not in PRESETS:
            raise ScreenError(f"unknown preset {preset!r}; one of {', '.join(PRESETS)}")
        filters = PRESETS[preset]["filters"] + filters
        sort = sort or PRESETS[preset].get("sort")
        rank = rank or PRESETS[preset].get("rank")
        preset_excludes = PRESETS[preset].get("exclude_sectors", [])
        exclude += [s for s in preset_excludes if s not in (sector or [])]
    parsed = [parse_filter(f) for f in filters]
    if rank not in (None, "magic"):
        raise ScreenError(f"unknown rank {rank!r}; only 'magic'")

    if as_of is None:
        as_of = session.scalar(select(func.max(CompanyMetrics.as_of)))
    if as_of is None:
        return None, []
    stmt = (
        select(CompanyMetrics, Security.ticker, Security.name, Issuer.sic)
        .join(Security, Security.id == CompanyMetrics.security_id)
        .join(Issuer, Issuer.cik == CompanyMetrics.cik)
        .where(CompanyMetrics.as_of == as_of)
    )
    try:
        include_ranges = [r for s in sector or [] for r in sectors.codes(s)]
        exclude_ranges = [r for s in exclude for r in sectors.codes(s)]
    except ValueError as exc:
        raise ScreenError(str(exc)) from None
    if include_ranges:
        stmt = stmt.where(or_(*(Issuer.sic.between(lo, hi) for lo, hi in include_ranges)))
    for lo, hi in exclude_ranges:  # companies without a SIC code stay in
        stmt = stmt.where(Issuer.sic.is_(None) | ~Issuer.sic.between(lo, hi))
    for f in parsed:
        column = getattr(CompanyMetrics, f.metric)
        stmt = stmt.where(column.is_not(None), OPS[f.op](column, f.value))
    rows = [
        {
            "ticker": ticker,
            "name": name,
            "security_id": m.security_id,
            "sector": sectors.sector(sic),
            "currency": m.currency,
            **{c: getattr(m, c) for c in ("as_of", "period_end", *NUMERIC)},
        }
        for m, ticker, name, sic in session.execute(stmt)
    ]

    if rank == "magic":
        rows = [r for r in rows if r["earnings_yield"] is not None and r["roic"] is not None]
        by_ey = sorted(rows, key=lambda r: -r["earnings_yield"])
        by_roic = sorted(rows, key=lambda r: -r["roic"])
        score = {id(r): i for i, r in enumerate(by_ey)}
        for i, r in enumerate(by_roic):
            score[id(r)] += i
        rows.sort(key=lambda r: (score[id(r)], r["ticker"] or ""))  # ties: deterministic
        for i, r in enumerate(rows, start=1):
            r["rank"] = i
    elif sort:
        descending = sort.startswith("-")
        metric = sort.lstrip("-")
        if metric not in NUMERIC:
            raise ScreenError(f"unknown sort metric {metric!r}")
        present = [r for r in rows if r[metric] is not None]
        missing = [r for r in rows if r[metric] is None]
        rows = sorted(present, key=lambda r: r[metric], reverse=descending) + missing
    return as_of, rows[:limit]
