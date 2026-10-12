"""Live distress alerts: H001's frozen model (docs/research/H001-distress-alerts.md) scored
every day for the companies we watch (the watchlist and current holdings).

The holdout tested "the top 2% of company-quarters by score", so a company is flagged when
its score is in the top 2% of the whole research population on that day, and the alert is a
company *entering* that group. A flag means "read the filings now", not "sell": in the
holdout about one flagged company in nine failed within a year (7.3 times the base rate),
with ten months' median warning.

Corporate events of watched companies are refreshed by the daily sync before scoring; the
rest of the population's events by the weekly one, so the cutoff can lag by a few days.
"""

import json
from dataclasses import dataclass
from datetime import date

import numpy as np
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from fin_intel.models import DistressScore, Issuer
from fin_intel.studies import h001


def watched(session: Session) -> dict[int, list[str]]:
    """CIK -> tickers of the watchlist (FI_WATCHLIST) and current holdings."""
    from fin_intel import portfolio, symbols
    from fin_intel.config import get_settings

    tickers = set(get_settings().watchlist_tickers)
    tickers |= {p.symbol for p in portfolio.positions(session) if p.quantity}
    out: dict[int, list[str]] = {}
    for ticker in sorted(tickers):
        security = symbols.get_security(session, ticker)
        if security is not None and security.cik is not None:
            out.setdefault(security.cik, []).append(ticker.upper())
    return out


def present(row: h001.Row) -> list[str]:
    """The signals behind a score, as the model names them (insider selling lowers it)."""
    return [n for n, on in h001.flags(row, h001.FROZEN["insider_threshold"]).items() if on]


def score_day(session: Session, day: date) -> tuple[list[tuple[h001.Row, float]], float]:
    """Every eligible member of the population scored on `day`, and the top-2% cutoff."""
    rows = h001.build_rows(session, day, day, days=[day])
    scored = [(r, h001.frozen_score(r)) for r in rows]
    if not scored:
        return [], float("nan")
    return scored, float(np.quantile([s for _, s in scored], 1 - h001.ALERT_RATE))


@dataclass
class Alert:
    cik: int
    name: str | None
    tickers: list[str]
    score: float
    cutoff: float
    signals: list[str]
    z: float | None
    new: bool  # entered the top 2% on this day


def sync(session: Session, day: date | None = None) -> list[Alert]:
    """Score the watched companies on `day` (today), store the scores and return the
    flagged ones, marking those that weren't flagged on their previous scoring."""
    day = day or date.today()
    companies = watched(session)
    if not companies:
        return []
    scored, cutoff = score_day(session, day)
    session.execute(delete(DistressScore).where(DistressScore.day == day))
    before = session.scalar(
        select(func.max(DistressScore.day)).where(
            DistressScore.cik.in_(companies), DistressScore.day < day
        )
    )
    previous = dict(
        session.execute(
            select(DistressScore.cik, DistressScore.flagged).where(DistressScore.day == before)
        ).all()
    )
    names = dict(
        session.execute(select(Issuer.cik, Issuer.name).where(Issuer.cik.in_(companies))).all()
    )
    alerts = []
    for row, score in scored:
        if row.cik not in companies:
            continue
        flagged = score >= cutoff
        signals = present(row)
        session.add(
            DistressScore(
                day=day, cik=row.cik, score=score, cutoff=cutoff, flagged=flagged,
                signals=json.dumps(signals), z=row.z,
            )
        )  # fmt: skip
        if flagged:
            alerts.append(
                Alert(
                    row.cik, names.get(row.cik), companies[row.cik], score, cutoff, signals,
                    row.z, new=not previous.get(row.cik, False),
                )
            )  # fmt: skip
    session.commit()
    return sorted(alerts, key=lambda a: -a.score)


def latest(session: Session) -> list[dict]:
    """The latest score of every watched company that has one, highest first; companies
    not scored (no annual report in 15 months, float under $75 million or untagged, as for
    most foreign filers) are listed with a reason."""
    companies = watched(session)
    last = session.scalar(select(func.max(DistressScore.day)))
    rows = {
        r.cik: r
        for r in session.scalars(
            select(DistressScore).where(DistressScore.day == last, DistressScore.cik.in_(companies))
        )
    }
    names = dict(
        session.execute(select(Issuer.cik, Issuer.name).where(Issuer.cik.in_(companies))).all()
    )
    out = []
    for cik, tickers in companies.items():
        r = rows.get(cik)
        base = {"cik": cik, "name": names.get(cik), "tickers": tickers}
        if r is None:
            reason = "outside the model: no recent annual report or float"
            out.append({**base, "day": last, "scored": False, "reason": reason})
        else:
            out.append({**base, "day": r.day, "scored": True, "score": r.score,
                        "cutoff": r.cutoff, "flagged": r.flagged,
                        "signals": json.loads(r.signals), "z": r.z})  # fmt: skip
    return sorted(out, key=lambda d: (not d["scored"], -d.get("score", 0)))
