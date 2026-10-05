"""Forward outcomes: what happened after each company metrics snapshot.

The labels research scores signals against (does a cheap, high-quality company with insider
buying go on to beat its sector?). For every company in a snapshot (company_metrics), from
the close after the snapshot date:

- total returns over 1, 3, 6 and 12 months (21, 63, 126, 252 trading days);
- excess returns over SPY, over the snapshot universe's median and over its sector's median
  (SIC division): fair benchmarks, since a cap-weighted index can be dominated by a few
  stocks;
- the 12-month maximum drawdown;
- the re-rating: the log change in P/E and EV/EBIT a year later (positive = more expensive).

A security that stops trading keeps its last price (no survivorship bias). Outcomes whose
horizon hasn't passed stay empty and fill in on later runs.
"""

import math
from collections import defaultdict
from datetime import date
from statistics import median

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from fin_intel import sectors, timeseries
from fin_intel.db import upsert
from fin_intel.models import CompanyMetrics, ForwardOutcome, Issuer

HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}


def _forward(px: list[float | None], start: int, days: int, n: int) -> float | None:
    first = px[start]
    if first is None or first <= 0 or start + days >= n:
        return None
    last = next(
        (v for i in range(start + days, start, -1) if (v := px[i]) is not None and v > 0), first
    )
    return last / first - 1


def _drawdown(px: list[float | None], start: int, days: int, n: int) -> float | None:
    if start + days >= n or px[start] is None:
        return None
    peak, worst = px[start], 0.0
    for v in px[start : start + days + 1]:
        if v is None or v <= 0:
            continue
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def _log_change(now: float | None, later: float | None) -> float | None:
    return math.log(later / now) if now and later and now > 0 and later > 0 else None


def compute(session: Session, dates: list[date] | None = None) -> int:
    """Outcomes for every company in the given snapshots (default: all of them)."""
    days = timeseries.calendar(session, None, None)
    index = {d: i for i, d in enumerate(days)}
    n = len(days)
    spy = timeseries._price(session, "SPY", days, "px")
    snapshots = dates or sorted(set(session.scalars(select(CompanyMetrics.as_of).distinct())))
    sic = dict(session.execute(select(Issuer.cik, Issuer.sic)).all())
    cache: dict[int, list[float | None] | None] = {}

    def prices(security_id: int) -> list[float | None] | None:
        if security_id not in cache:
            try:
                cache[security_id] = timeseries.security_prices(session, security_id, days)
            except timeseries.SpecError:
                cache[security_id] = None
        return cache[security_id]

    later_snapshot = {
        d: next((s for s in snapshots if (s - d).days >= 360), None) for d in snapshots
    }
    written = 0
    for as_of in snapshots:
        start = next((index[d] for d in days if d > as_of), None)
        if start is None:
            continue
        metrics = session.scalars(select(CompanyMetrics).where(CompanyMetrics.as_of == as_of)).all()
        later = {}
        if later_snapshot[as_of]:
            later = {
                m.security_id: m
                for m in session.scalars(
                    select(CompanyMetrics).where(CompanyMetrics.as_of == later_snapshot[as_of])
                )
            }
        rows = []
        for m in metrics:
            px = prices(m.security_id)
            if px is None:
                continue
            ahead = later.get(m.security_id)
            row = {
                "security_id": m.security_id,
                "as_of": as_of,
                "cik": m.cik,
                "sector": sectors.sector(sic.get(m.cik)),
                **{f"ret_{k}": _forward(px, start, h, n) for k, h in HORIZONS.items()},
                "max_drawdown_12m": _drawdown(px, start, HORIZONS["12m"], n),
                "pe_change_12m": _log_change(m.pe, ahead.pe if ahead else None),
                "ev_ebit_change_12m": _log_change(m.ev_ebit, ahead.ev_ebit if ahead else None),
            }
            rows.append(row)
        for k in ("3m", "12m"):
            spy_ret = _forward(spy, start, HORIZONS[k], n)
            values = [r[f"ret_{k}"] for r in rows if r[f"ret_{k}"] is not None]
            universe = median(values) if values else None
            by_sector = defaultdict(list)
            for r in rows:
                if r[f"ret_{k}"] is not None and r["sector"]:
                    by_sector[r["sector"]].append(r[f"ret_{k}"])
            sector_median = {s: median(v) for s, v in by_sector.items() if len(v) >= 5}
            for r in rows:
                ret = r[f"ret_{k}"]
                r[f"excess_spy_{k}"] = None if ret is None or spy_ret is None else ret - spy_ret
                r[f"excess_universe_{k}"] = (
                    None if ret is None or universe is None else ret - universe
                )
                peer = sector_median.get(r["sector"] or "")
                r[f"excess_sector_{k}"] = None if ret is None or peer is None else ret - peer
        session.execute(delete(ForwardOutcome).where(ForwardOutcome.as_of == as_of))
        written += upsert(session, ForwardOutcome, rows, key=["security_id", "as_of"])
        session.commit()
    return written
