"""One point-in-time timeline across sources: what became public, when, about whom.

Each source contributes events dated by when they became public (a Form 4's filing date,
not the trade date; a congressional report's filing date; a lobbying quarter's posting
date; a month of contracts once USAspending has it), so a timeline cut at any date shows
only what could have been known then. Events are read from the source tables on request
(nothing is copied, nothing to rebuild), for a company (`ciks`), a theme, or both:

- company events: insider open-market trades, congressional trades, 8-K items and other
  event filings, periodic reports, short-interest jumps, 13F holders per quarter;
- theme events: bills (introduced, enacted) by policy area and subject, presidential
  documents by the theme's query, lobbying reports by issue code, monthly federal contract
  obligations by NAICS, and the company events of the theme's companies (graph.py's
  `part_of` edges, so `sync-industries` must have run);
- macro events: scheduled economic releases (CPI, payrolls, GDP, FOMC and BoJ decisions);
  for a theme, those updating its FRED series;
- with neither, everything in the window, capped per source.

Sources register with `@source("kind")`; `query()` merges them newest first.
"""

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select, true
from sqlalchemy.orm import Session

from fin_intel import contracts, filing_events, releases, shortinterest, themes
from fin_intel.models import (
    Bill,
    BillSubject,
    CongressReport,
    CongressReportMember,
    CongressTrade,
    CorporateEvent,
    CusipMapping,
    EconomicRelease,
    EconomicReleaseDate,
    FederalObligation,
    Filing,
    GraphEdge,
    InsiderTransaction,
    InstitutionalPosition,
    LegislatorTerm,
    LobbyingIssue,
    LobbyingReport,
    PresidentialDocument,
    PresidentialText,
    Security,
    ShortInterest,
)

PERIODIC_FORMS = ("10-K", "10-Q", "20-F", "40-F", "10-K/A", "20-F/A")
# Smaller insider trades are mostly plan purchases and small sales: noise on a timeline.
MIN_INSIDER_VALUE = 10_000
# ts_rank_cd of a presidential document's text against a theme's keywords: below this, the
# match is usually boilerplate (measured on 2025-2026 documents).
PRESIDENTIAL_MIN_RANK = 0.5
# Short interest changes between settlements smaller than this aren't events.
SHORT_CHANGE = 0.25
# 13F reports are due 45 days after the quarter: the quarter's holdings count as public then.
THIRTEENF_DEADLINE = timedelta(days=45)


@dataclass
class Event:
    date: date  # when it became public
    kind: str
    title: str
    cik: int | None = None
    ticker: str | None = None
    amount: float | None = None  # USD, when the event has one
    actor: str | None = None  # insider, member of Congress, lobbying client, president
    ref: str = ""  # source table and key


@dataclass
class Query:
    start: date
    end: date
    ciks: set[int] | None = None
    theme: themes.Theme | None = None
    limit: int = 200  # per source
    tickers: dict[int, str] = field(default_factory=dict)  # cik -> ticker, for titles

    @property
    def scoped(self) -> bool:
        return self.ciks is not None or self.theme is not None


Source = Callable[[Session, Query], list[Event]]
SOURCES: dict[str, Source] = {}


def source(kind: str) -> Callable[[Source], Source]:
    def register(fn: Source) -> Source:
        SOURCES[kind] = fn
        return fn

    return register


def theme_ciks(session: Session, theme: themes.Theme) -> set[int]:
    rows = session.scalars(
        select(GraphEdge.src).where(
            GraphEdge.dst == f"theme:{theme.name}", GraphEdge.kind == "part_of"
        )
    )
    return {int(key.split(":", 1)[1]) for key in rows if key.startswith("company:")}


def query(
    session: Session,
    start: date,
    end: date,
    *,
    ciks: set[int] | None = None,
    theme: str | None = None,
    kinds: list[str] | None = None,
    limit: int = 200,
) -> list[Event]:
    """Events public in [start, end], newest first. A theme widens `ciks` to its
    companies for company events and adds its policy events."""
    t = themes.get(theme) if theme else None
    if t is not None and ciks is None:
        ciks = theme_ciks(session, t)
    q = Query(start, end, ciks, t, limit)
    q.tickers = _tickers(session, ciks)
    unknown = set(kinds or ()) - set(SOURCES)
    if unknown:
        raise ValueError(f"unknown kinds {sorted(unknown)}; one of {', '.join(SOURCES)}")
    events: list[Event] = []
    for kind, fn in SOURCES.items():
        if kinds and kind not in kinds:
            continue
        events += fn(session, q)
    return sorted(events, key=lambda e: (e.date, e.kind), reverse=True)


def _tickers(session: Session, ciks: set[int] | None) -> dict[int, str]:
    if not ciks:
        return {}
    rows = session.execute(
        select(Security.cik, Security.ticker)
        .where(Security.cik.in_(ciks), Security.active, Security.ticker.is_not(None))
        .order_by(Security.cik, Security.ticker.desc())
    )
    return {cik: ticker for cik, ticker in rows}


def _company_filter(q: Query, column: Any) -> Any | None:
    """None: skip the source (scoped to a theme with no companies, or to policy only)."""
    if q.ciks is not None:
        return column.in_(q.ciks) if q.ciks else None
    return None if q.scoped else true()


# --- company events ---


@source("insider")
def _insiders(session: Session, q: Query) -> list[Event]:
    """Open-market purchases and sales (codes P and S) of at least MIN_INSIDER_VALUE, on
    the Form 4 filing date."""
    where = _company_filter(q, InsiderTransaction.issuer_cik)
    if where is None:
        return []
    rows = session.scalars(
        select(InsiderTransaction)
        .where(
            where,
            InsiderTransaction.trans_code.in_(("P", "S")),
            InsiderTransaction.shares * InsiderTransaction.price >= MIN_INSIDER_VALUE,
            InsiderTransaction.filing_date.between(q.start, q.end),
        )
        .order_by(InsiderTransaction.filing_date.desc())
        .limit(q.limit)
    )
    out = []
    for r in rows:
        verb = "bought" if r.trans_code == "P" else "sold"
        amount = r.shares * r.price if r.shares and r.price else None
        who = r.owner_name or "insider"
        role = f" ({r.owner_title or r.relationship})" if (r.owner_title or r.relationship) else ""
        out.append(
            Event(
                r.filing_date,
                "insider",
                f"{who}{role} {verb} {r.shares or 0:,.0f} shares"
                + (f" at {r.price:,.2f}" if r.price else ""),
                cik=r.issuer_cik,
                ticker=q.tickers.get(r.issuer_cik) or r.issuer_symbol,
                amount=amount,
                actor=who,
                ref=f"insider_transactions:{r.key}",
            )
        )
    return out


@source("congress")
def _congress(session: Session, q: Query) -> list[Event]:
    """Trades in the companies' stocks, on the day the report was filed (public)."""
    if q.ciks is not None:
        tickers = list(q.tickers.values())
        if not tickers:
            return []
        where = CongressTrade.ticker.in_(tickers)
    elif q.scoped:
        return []
    else:
        where = true()
    public = func.coalesce(CongressReport.filed, CongressTrade.notified)
    member = (
        select(func.concat(LegislatorTerm.first, " ", LegislatorTerm.last))
        .where(LegislatorTerm.bioguide == CongressReportMember.bioguide)
        .limit(1)
        .scalar_subquery()
    )
    rows = session.execute(
        select(CongressTrade, public, func.coalesce(member, CongressReport.name))
        .join(CongressReport, CongressReport.doc_id == CongressTrade.doc_id)
        .outerjoin(CongressReportMember, CongressReportMember.doc_id == CongressTrade.doc_id)
        .where(where, public.between(q.start, q.end))
        .order_by(public.desc())
        .limit(q.limit)
    ).all()
    by_ticker = {t: c for c, t in q.tickers.items()}
    out = []
    for trade, day, name in rows:
        span = ""
        if trade.amount_min is not None:
            span = f" ${trade.amount_min:,.0f}-{trade.amount_max or trade.amount_min:,.0f}"
        out.append(
            Event(
                day,
                "congress",
                f"{name or 'member'} {trade.trans_type or 'traded'} "
                f"{trade.ticker or trade.asset_name or ''}{span} (traded {trade.trans_date})",
                cik=by_ticker.get(trade.ticker or ""),
                ticker=trade.ticker,
                amount=trade.amount_min,
                actor=name,
                ref=f"congress_trades:{trade.key}",
            )
        )
    return out


@source("filing_event")
def _filing_events(session: Session, q: Query) -> list[Event]:
    """8-K items and event forms (13D/G stakes, late filings, delistings, tender offers)."""
    where = _company_filter(q, CorporateEvent.cik)
    if where is None:
        return []
    rows = session.scalars(
        select(CorporateEvent)
        .where(where, CorporateEvent.filed.between(q.start, q.end))
        .order_by(CorporateEvent.filed.desc())
        .limit(q.limit)
    )
    out = []
    for r in rows:
        if r.item:
            title = f"8-K {r.item}: {filing_events.ITEMS.get(r.item, 'item')}"
        else:
            title = f"{r.form}: {filing_events.FORMS.get(r.form, r.form)}"
        out.append(
            Event(
                r.filed,
                "filing_event",
                title,
                cik=r.cik,
                ticker=q.tickers.get(r.cik),
                ref=f"corporate_events:{r.accession}:{r.item}",
            )
        )
    return out


def _securities(session: Session, ciks: set[int]) -> dict[int, int]:
    """security id -> cik, for the companies' listings (active or not)."""
    rows = session.execute(select(Security.id, Security.cik).where(Security.cik.in_(ciks)))
    return {sid: cik for sid, cik in rows}


@source("short_interest")
def _short_interest(session: Session, q: Query) -> list[Event]:
    """Changes of SHORT_CHANGE or more between settlements, when FINRA publishes them.
    Companies only (market-wide, every stock moves somewhere)."""
    if not q.ciks:
        return []
    securities = _securities(session, q.ciks)
    rows = session.scalars(
        select(ShortInterest)
        .where(
            ShortInterest.security_id.in_(securities),
            ShortInterest.settlement_date.between(q.start - timedelta(days=60), q.end),
        )
        .order_by(ShortInterest.security_id, ShortInterest.settlement_date)
    ).all()
    out = []
    for before, after in zip(rows, rows[1:], strict=False):
        if before.security_id != after.security_id or not before.short_position:
            continue
        change = after.short_position / before.short_position - 1
        public = shortinterest.available_on(after.settlement_date)
        if abs(change) < SHORT_CHANGE or not q.start <= public <= q.end:
            continue
        cover = f", {after.days_to_cover:.1f} days to cover" if after.days_to_cover else ""
        cik = securities[after.security_id]
        out.append(
            Event(
                public,
                "short_interest",
                f"Short interest {change:+.0%} to {after.short_position:,.0f} shares{cover} "
                f"(settled {after.settlement_date})",
                cik=cik,
                ticker=q.tickers.get(cik),
                ref=f"short_interest:{after.security_id}:{after.settlement_date}",
            )
        )
    return out[-q.limit :]


@source("13f")
def _thirteenf(session: Session, q: Query) -> list[Event]:
    """Each quarter's institutional holders against the quarter before, on the filing
    deadline (holdings filed by then; late filers are left out). Companies only."""
    if not q.ciks:
        return []
    by_cusip = dict(
        session.execute(
            select(CusipMapping.cusip, Security.cik)
            .join(Security, Security.id == CusipMapping.security_id)
            .where(Security.cik.in_(q.ciks))
        ).all()
    )
    if not by_cusip:
        return []
    first = q.start - THIRTEENF_DEADLINE - timedelta(days=100)  # and the quarter before
    rows = session.execute(
        select(
            InstitutionalPosition.cusip,
            InstitutionalPosition.period,
            InstitutionalPosition.filer_cik,
            func.sum(InstitutionalPosition.shares),
        )
        .where(
            InstitutionalPosition.cusip.in_(by_cusip),
            InstitutionalPosition.put_call == "",
            InstitutionalPosition.period >= first,
            InstitutionalPosition.filed <= InstitutionalPosition.period + THIRTEENF_DEADLINE,
        )
        .group_by(
            InstitutionalPosition.cusip,
            InstitutionalPosition.period,
            InstitutionalPosition.filer_cik,
        )
    ).all()
    holders: dict[tuple[int, date], set[int]] = defaultdict(set)
    shares: dict[tuple[int, date], float] = defaultdict(float)
    for cusip, period, filer, held in rows:
        holders[(by_cusip[cusip], period)].add(filer)
        shares[(by_cusip[cusip], period)] += held or 0.0
    out = []
    for (cik, period), managers in sorted(holders.items()):
        public = period + THIRTEENF_DEADLINE
        if not q.start <= public <= q.end:
            continue
        earlier = [p for c, p in holders if c == cik and p < period]
        change = ""
        if earlier:
            prev = (cik, max(earlier))
            change = f" ({len(managers) - len(holders[prev]):+,})"
            if shares[prev]:
                change += f", shares held {shares[(cik, period)] / shares[prev] - 1:+.1%}"
        out.append(
            Event(
                public,
                "13f",
                f"13F for quarter ending {period}: {len(managers):,} holders{change}",
                cik=cik,
                ticker=q.tickers.get(cik),
                ref=f"institutional_positions:{cik}:{period}",
            )
        )
    return out[-q.limit :]


@source("report")
def _reports(session: Session, q: Query) -> list[Event]:
    """Periodic reports (10-K, 10-Q, 20-F, 40-F), on the filing date."""
    where = _company_filter(q, Filing.cik)
    if where is None:
        return []
    rows = session.scalars(
        select(Filing)
        .where(where, Filing.form.in_(PERIODIC_FORMS), Filing.filed.between(q.start, q.end))
        .order_by(Filing.filed.desc())
        .limit(q.limit)
    )
    return [
        Event(
            r.filed,
            "report",
            f"{r.form} for {r.fiscal_period or ''} {r.fiscal_year or ''}".strip(),
            cik=r.cik,
            ticker=q.tickers.get(r.cik),
            ref=f"filings:{r.accession}",
        )
        for r in rows
        if r.filed
    ]


# --- policy events (themes) ---


def _matches(column: Any, keywords: str | None) -> Any:
    """Full-text match of a text column (no index: callers narrow by date or code first)."""
    if not keywords:
        return true()
    vector = func.to_tsvector("english", func.coalesce(column, ""))
    return vector.op("@@")(func.websearch_to_tsquery("english", keywords))


def _policy(q: Query) -> bool:
    """Policy sources answer theme queries and unscoped ones, not company-only ones."""
    return q.theme is not None or not q.scoped


@source("bill")
def _bills(session: Session, q: Query) -> list[Event]:
    """Bills introduced and enacted: for a theme, those whose title matches its keywords,
    or whose policy area and one of whose subjects are both the theme's (either alone is
    too broad: "Science, Technology, Communications" covers spam calls)."""
    if not _policy(q):
        return []
    where: Any = true()
    if q.theme is not None:
        subjects = select(BillSubject.bill_id).where(BillSubject.subject.in_(q.theme.bill_subjects))
        structured = and_(Bill.policy_area.in_(q.theme.policy_areas), Bill.id.in_(subjects))
        where = or_(_matches(Bill.title, q.theme.keywords), structured)
    out = []
    for column, kind in ((Bill.introduced, "bill"), (Bill.enacted, "law")):
        rows = session.scalars(
            select(Bill)
            .where(where, column.between(q.start, q.end))
            .order_by(column.desc())
            .limit(q.limit)
        )
        for b in rows:
            verb = "introduced" if kind == "bill" else f"enacted (P.L. {b.law})"
            out.append(
                Event(
                    getattr(b, column.key),
                    "bill",
                    f"{b.id} {verb}: {b.title or ''}"[:300],
                    actor=b.sponsor,
                    ref=f"bills:{b.id}",
                )
            )
    return out


@source("presidential")
def _presidential(session: Session, q: Query) -> list[Event]:
    """Executive orders, proclamations, memoranda... on the signing date (announced that
    day; the Federal Register follows days later). For a theme: its keywords in the title,
    or a text match ranking at least PRESIDENTIAL_MIN_RANK (boilerplate mentions, such as
    "national security" once in an order on fishing, rank lower)."""
    if not _policy(q):
        return []
    stmt = select(PresidentialDocument).where(PresidentialDocument.signed.between(q.start, q.end))
    if q.theme is not None:
        if not q.theme.keywords:
            return []
        tsq = func.websearch_to_tsquery("english", q.theme.keywords)
        stmt = stmt.join(
            PresidentialText,
            PresidentialText.document_number == PresidentialDocument.document_number,
        ).where(
            or_(
                _matches(PresidentialDocument.title, q.theme.keywords),
                and_(
                    PresidentialText.search.op("@@")(tsq),
                    func.ts_rank_cd(PresidentialText.search, tsq) >= PRESIDENTIAL_MIN_RANK,
                ),
            )
        )
    rows = session.scalars(stmt.order_by(PresidentialDocument.signed.desc()).limit(q.limit))
    return [
        Event(
            d.signed,
            "presidential",
            f"{d.kind.replace('_', ' ')} {d.number or ''}: {d.title or ''}".strip()[:300],
            actor=d.president,
            ref=f"presidential_documents:{d.document_number}",
        )
        for d in rows
        if d.signed
    ]


@source("lobbying")
def _lobbying(session: Session, q: Query) -> list[Event]:
    """Lobbying reports on the theme's issue codes whose issue description matches its
    keywords (codes alone are coarse: TRD is all of trade), on the posting date, largest
    first within the cap."""
    if q.theme is None:
        return []  # unscoped: ~25k reports a quarter would drown everything else
    issues = select(LobbyingIssue.filing_uuid).where(
        LobbyingIssue.code.in_(q.theme.lobbying),
        _matches(LobbyingIssue.description, q.theme.keywords),
    )
    rows = session.scalars(
        select(LobbyingReport)
        .where(
            LobbyingReport.filing_uuid.in_(issues),
            LobbyingReport.posted.between(q.start, q.end),
            LobbyingReport.amount > 0,
        )
        .order_by(LobbyingReport.amount.desc())
        .limit(q.limit)
    )
    return [
        Event(
            r.posted,
            "lobbying",
            f"{r.client or '?'}: ${r.amount:,.0f} lobbying via {r.registrant or '?'} "
            f"({r.year} Q{r.quarter})",
            amount=r.amount,
            actor=r.client,
            ref=f"lobbying_reports:{r.filing_uuid}",
        )
        for r in rows
        if r.posted
    ]


@source("contracts")
def _contracts(session: Session, q: Query) -> list[Event]:
    """The theme's federal contract obligations per month, once USAspending has the month,
    with the change from a year earlier."""
    if q.theme is None or not q.theme.naics:
        return []
    prefix = or_(*(FederalObligation.naics.like(f"{p}%") for p in q.theme.naics))
    rows = session.execute(
        select(FederalObligation.month, func.sum(FederalObligation.amount))
        .where(prefix, FederalObligation.month >= q.start - timedelta(days=500))
        .group_by(FederalObligation.month)
    ).all()
    totals = dict(rows)
    out = []
    for month, amount in sorted(totals.items()):
        public = contracts.available_on(month)
        if not q.start <= public <= q.end:
            continue
        before = totals.get(month.replace(year=month.year - 1))
        change = f", {amount / before - 1:+.0%} on a year earlier" if before else ""
        out.append(
            Event(
                public,
                "contracts",
                f"{q.theme.label}: ${amount / 1e9:,.2f}B federal obligations in {month:%Y-%m}"
                + change,
                amount=amount,
                ref=f"federal_obligations:{month}",
            )
        )
    return out[-q.limit :]


# --- macro events ---


@source("release")
def _releases(session: Session, q: Query) -> list[Event]:
    """Economic releases on their publication day (scheduled in advance, so public as a
    date even before; the figures themselves come out that day). Releases of daily series
    aren't events. For a theme: the releases updating its FRED series."""
    if not _policy(q):
        return []
    series, daily = releases.release_series(session)
    where: Any = EconomicRelease.id.not_in(daily) if daily else true()
    if q.theme is not None:
        ids = [r for r, ids in series.items() if r not in daily and set(ids) & set(q.theme.fred)]
        if not ids:
            return []
        where = EconomicRelease.id.in_(ids)
    rows = session.execute(
        select(EconomicReleaseDate.date, EconomicRelease.id, EconomicRelease.name)
        .join(EconomicRelease, EconomicRelease.id == EconomicReleaseDate.release_id)
        .where(where, EconomicReleaseDate.date.between(q.start, q.end))
        .order_by(EconomicReleaseDate.date.desc())
        .limit(q.limit)
    ).all()
    out = []
    for day, rid, name in rows:
        updates = series.get(rid, [])
        if q.theme is not None:
            updates = [s for s in updates if s in q.theme.fred]
        detail = f" ({', '.join(updates[:6])})" if updates else ""
        out.append(Event(day, "release", f"{name or rid}{detail}", ref=f"economic_releases:{rid}"))
    return out
