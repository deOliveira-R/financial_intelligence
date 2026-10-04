"""One company, several listings: link non-US issuers to the shares they trade in the US.

A company known from its home regulator (EDINET, DART, TWSE, ESEF) often also trades in
the US: its ordinary shares over the counter, or depositary receipts (ADRs). Those US
listings carry prices we already have (Massive), which is how such companies get valued
without a home-market price feed.

- Ordinary shares are matched by identifier: OpenFIGI's share-class FIGI is the same on
  every exchange, so the home listing's share class (from its ticker, or from an ISIN for
  European issuers via GLEIF) finds the US OTC line exactly. Priced in USD per ordinary
  share, it needs no conversion ratio.
- ADRs have their own share class. They're matched by name to the company's ordinary line
  (both names from OpenFIGI, which names consistently), and their ratio (ordinary shares
  per ADR) is inferred from the two prices when the ordinary line also trades. An ADR
  without a ratio stays linked but unvalued.
"""

import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fin_intel.models import DailyBar, Issuer, Security, StatementItem

RATIO_WINDOW_DAYS = 180
HOME_EXCHANGES = {"edinet": ["JT"], "dart": ["KS", "KQ"], "twse": ["TT"]}
COMMON = ("Common Stock", "Ordinary Shares")
NICE_RATIOS = (0.1, 0.125, 0.2, 0.25, 1 / 3, 0.5, 1, 2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 40, 50, 100)
_ADR_MARKERS = re.compile(r"[-\s/]+(UNSP|UNSPON|SPON|SPONS|SP)?\s*/?\s*(ADR|ADS|GDR)\b.*$", re.I)
_LEGAL = set(
    "co corp corporation company ltd limited inc plc ag sa se nv spa ab asa oyj kk the".split()
)


def home_jobs(session: Session) -> list[tuple[dict[str, str], int]]:
    """OpenFIGI jobs (ticker on its home exchange) for issuers without a share class yet."""
    out = []
    for issuer in session.scalars(
        select(Issuer).where(
            Issuer.source.in_(list(HOME_EXCHANGES)),
            Issuer.home_ticker.is_not(None),
            Issuer.share_class_figi.is_(None),
        )
    ):
        for exchange in HOME_EXCHANGES[issuer.source]:
            job = {"idType": "TICKER", "idValue": issuer.home_ticker, "exchCode": exchange}
            out.append((job, issuer.cik))
    return out


def isin_jobs(isins_by_cik: dict[int, list[str]]) -> list[tuple[dict[str, str], int]]:
    return [
        ({"idType": "ID_ISIN", "idValue": isin}, cik)
        for cik, isins in isins_by_cik.items()
        for isin in isins
    ]


def _common(result: dict[str, Any]) -> dict[str, Any] | None:
    return next(
        (d for d in (result or {}).get("data") or [] if d.get("securityType") in COMMON), None
    )


def apply_home(
    session: Session, jobs: list[tuple[dict[str, str], int]], results: list[dict[str, Any]]
) -> int:
    """Record each issuer's share class from the first job that found its common shares."""
    done = 0
    for (job, cik), result in zip(jobs, results, strict=False):
        found = _common(result)
        issuer = session.get(Issuer, cik)
        if found is None or issuer is None or issuer.share_class_figi:
            continue
        issuer.share_class_figi = found.get("shareClassFIGI")
        issuer.figi_name = found.get("name")
        if job["idType"] == "ID_ISIN":
            issuer.isin = job["idValue"]
        done += 1
    return done


def us_jobs(session: Session) -> list[tuple[dict[str, str], int]]:
    """OpenFIGI jobs for US-traded securities that could be a foreign company's shares:
    OTC ordinary lines and ADRs without a share class or issuer yet, and SEC filers' ADRs
    (to recognize companies that file with both the SEC and their home regulator)."""
    rows = session.scalars(
        select(Security).where(
            Security.active,
            Security.ticker.is_not(None),
            Security.security_type.in_(("OS", "ADRC")),
            Security.figi_name.is_(None),
            (Security.share_class_figi.is_(None) & Security.cik.is_(None))
            | ((Security.security_type == "ADRC") & (Security.cik < 10**10)),
        )
    )
    return [
        ({"idType": "TICKER", "idValue": s.ticker.replace("-", "/"), "exchCode": "US"}, s.id)
        for s in rows
        if s.ticker
    ]


def apply_us(
    session: Session, jobs: list[tuple[dict[str, str], int]], results: list[dict[str, Any]]
) -> int:
    done = 0
    for (_, security_id), result in zip(jobs, results, strict=False):
        data = ((result or {}).get("data") or [None])[0]
        security = session.get(Security, security_id)
        if not data or security is None:
            continue
        security.share_class_figi = data.get("shareClassFIGI")
        security.figi_name = data.get("name")
        done += 1
    return done


def _tokens(name: str | None) -> list[str]:
    name = _ADR_MARKERS.sub("", (name or "").replace("&", " AND "))
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower().replace("-", "")).split()
    return [w for w in words if w not in _LEGAL]


def _same_name(adr: list[str], ordinary: list[str]) -> bool:
    """Every word of the shorter name starts a word of the longer, in order (OpenFIGI
    abbreviates: SCHNEIDER ELECT SE ~ SCHNEIDER ELECTRIC SE); first words agree."""
    if not adr or not ordinary or adr[0] != ordinary[0]:
        return False
    short, long_ = sorted((adr, ordinary), key=len)
    j = 0
    for word in short:
        while j < len(long_) and not long_[j].startswith(word):
            j += 1
        if j == len(long_):
            return False
        j += 1
    return True


def _nice(ratio: float) -> float | None:
    best = min(NICE_RATIOS, key=lambda r: abs(r - ratio) / r)
    # ADR and ordinary prices differ by a few percent (timing, spreads); between two
    # plausible ratios is ambiguous, so it stays unknown.
    return best if abs(best - ratio) / best <= 0.08 else None


def link(session: Session, today: date | None = None) -> dict[str, int]:
    """Attach US listings to foreign issuers: ordinary lines by share class, ADRs by name
    (with depositary shares where the ratio can be inferred)."""
    today = today or date.today()
    issuers = {
        i.share_class_figi: i
        for i in session.scalars(select(Issuer).where(Issuer.share_class_figi.is_not(None)))
    }
    stats = {"ordinary": 0, "adr": 0, "adr_valued": 0}
    ordinary_by_cik: dict[int, Security] = {}
    for s in session.scalars(
        select(Security).where(
            Security.share_class_figi.in_(list(issuers)), Security.currency.is_(None)
        )
    ):
        issuer = issuers[s.share_class_figi]
        if s.cik is None and s.security_type != "ADRC":
            s.cik = issuer.cik
            stats["ordinary"] += 1
        if s.cik == issuer.cik:
            ordinary_by_cik[issuer.cik] = s

    candidates = defaultdict(list)
    for issuer in issuers.values():
        tokens = _tokens(issuer.figi_name)
        if tokens:
            candidates[tokens[0]].append((tokens, issuer))
    # Ratios rarely change, and OTC ordinary lines trade only now and then: compare each
    # ordinary-line trade with the ADR's close that day or the day before, over half a year.
    since = today - timedelta(days=RATIO_WINDOW_DAYS)
    stats["same_as_sec"] = 0
    for adr in session.scalars(
        select(Security).where(Security.security_type == "ADRC", Security.active)
    ):
        tokens = _tokens(adr.figi_name or adr.name)
        matches = [
            i for t, i in candidates.get(tokens[0] if tokens else "", []) if _same_name(tokens, t)
        ]
        if len(matches) != 1:
            continue
        issuer = matches[0]
        if adr.cik is not None and adr.cik < 10**10:
            # An SEC filer's ADR: the home issuer is the same company, valued from its SEC
            # filings (matched only on OpenFIGI's own names, not filers' names).
            if adr.figi_name and issuer.same_as is None:
                issuer.same_as = adr.cik
                stats["same_as_sec"] += 1
            continue
        if adr.cik is None:
            adr.cik = issuer.cik
            stats["adr"] += 1
        ordinary = ordinary_by_cik.get(issuer.cik)
        if ordinary is None:
            continue
        closes = defaultdict(dict)
        for sid, day, close in session.execute(
            select(DailyBar.security_id, DailyBar.date, DailyBar.close).where(
                DailyBar.security_id.in_([adr.id, ordinary.id]), DailyBar.date >= since
            )
        ):
            closes[day][sid] = close
        adr_close = {d: c[adr.id] for d, c in closes.items() if c.get(adr.id)}
        both = []
        for day, c in closes.items():
            if not c.get(ordinary.id):
                continue
            near = adr_close.get(day) or adr_close.get(day - timedelta(days=1))
            if near:
                both.append(near / c[ordinary.id])
        ratio = _nice(median(both)) if len(both) >= 3 else None
        shares = session.scalar(
            select(StatementItem.value)
            .where(StatementItem.cik == issuer.cik, StatementItem.line_item == "shares_outstanding")
            .order_by(StatementItem.period_end.desc())
            .limit(1)
        )
        if ratio and shares:
            adr.shares_outstanding = shares / ratio  # depositary receipts' worth of shares
            adr.shares_as_of = today
            stats["adr_valued"] += 1
    return stats


def trading_days(session: Session, ids: list[int], days: int = 30) -> dict[int, int]:
    """Bars per security in the last `days` (a liquidity floor for OTC lines)."""
    since = date.today() - timedelta(days=days)
    return dict(
        session.execute(
            select(DailyBar.security_id, func.count())
            .where(DailyBar.security_id.in_(ids), DailyBar.date >= since)
            .group_by(DailyBar.security_id)
        ).all()
    )
