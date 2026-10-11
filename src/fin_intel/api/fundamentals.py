"""Fundamentals: facts, filings, fiscal calendars, statements, company metrics, the
screener, filing text."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from fin_intel import (
    filing_text,
    screener,
    symbols,
)
from fin_intel.api.app import Orm, SessionDep, issuer_cik_or_404, security_or_404
from fin_intel.fundamentals import Fact, derive_q4, latest_per_period, split_adjust
from fin_intel.models import (
    CompanyMetrics,
    Concept,
    CorporateAction,
    Filing,
    FiscalCalendar,
    StatementItem,
)
from fin_intel.models import Fact as FactRow

router = APIRouter()


class ConceptOut(BaseModel):
    taxonomy: str
    concept: str
    label: str | None
    unit: str
    facts: int


class FactOut(Orm):
    unit: str
    period_start: date
    period_end: date
    period_type: str | None
    value: float
    fiscal_year: int | None
    fiscal_period: str | None
    form: str | None
    filed: date | None
    accession: str | None
    split_adjustment: float
    derived: bool


class FilingOut(Orm):
    accession: str
    form: str | None
    filed: date | None
    report_period_end: date | None
    fiscal_year: int | None
    fiscal_period: str | None


class CalendarOut(Orm):
    segment: int
    year_end_month: int
    year_end_day: int
    year_offset: int
    first_year_end: date
    last_year_end: date


@router.get("/fundamentals/{ticker}/concepts", response_model=list[ConceptOut])
def fundamental_concepts(session: SessionDep, ticker: str) -> list[ConceptOut]:
    cik = issuer_cik_or_404(session, ticker)
    stmt = (
        select(Concept.taxonomy, Concept.name, Concept.label, FactRow.unit, func.count())
        .join(FactRow, FactRow.concept_id == Concept.id)
        .where(FactRow.cik == cik)
        .group_by(Concept.taxonomy, Concept.name, Concept.label, FactRow.unit)
        .order_by(Concept.taxonomy, Concept.name)
    )
    return [
        ConceptOut(taxonomy=t, concept=c, label=lbl, unit=u, facts=n)
        for t, c, lbl, u, n in session.execute(stmt)
    ]


@router.get("/fundamentals/{ticker}/filings", response_model=list[FilingOut])
def filings(session: SessionDep, ticker: str, form: str | None = None) -> list[Filing]:
    cik = issuer_cik_or_404(session, ticker)
    stmt = select(Filing).where(Filing.cik == cik).order_by(Filing.filed.desc())
    if form:
        stmt = stmt.where(Filing.form == form)
    return list(session.scalars(stmt))


@router.get("/fundamentals/{ticker}/calendar", response_model=list[CalendarOut])
def fiscal_calendar(session: SessionDep, ticker: str) -> list[FiscalCalendar]:
    """The inferred fiscal year end(s); more than one row means the company changed it."""
    cik = issuer_cik_or_404(session, ticker)
    return list(
        session.scalars(
            select(FiscalCalendar).where(FiscalCalendar.cik == cik).order_by(FiscalCalendar.segment)
        )
    )


@router.get("/fundamentals/{ticker}", response_model=list[FactOut])
def fundamentals(
    session: SessionDep,
    ticker: str,
    concept: str,
    unit: str | None = Query(None, description="e.g. USD, USD/shares, shares; default: all"),
    period_type: str | None = Query(
        None, description="annual, quarter, half, nine_months, instant or other"
    ),
    form: str | None = Query(None, description="e.g. 10-K or 10-Q"),
    as_reported: bool = Query(
        False, description="Every filing's value as filed, instead of the latest per period"
    ),
    split_adjusted: bool = Query(
        True, description="Restate share counts and per-share values for later stock splits"
    ),
    fill_q4: bool = Query(
        True, description="Derive missing Q4 values as FY minus 9M (monetary flows only)"
    ),
) -> list[Fact]:
    security = security_or_404(session, ticker)
    if security.cik is None:
        raise HTTPException(404, f"{ticker} has no SEC filings")
    stmt = (
        select(
            Concept.name,
            FactRow.unit,
            FactRow.period_start,
            FactRow.period_end,
            FactRow.period_type,
            FactRow.value,
            FactRow.fiscal_year,
            FactRow.fiscal_period,
            Filing.form,
            Filing.filed,
            Filing.accession,
        )
        .join(Concept, Concept.id == FactRow.concept_id)
        .join(Filing, Filing.id == FactRow.filing_id)
        .where(FactRow.cik == security.cik, Concept.name == concept)
    )
    if unit:
        stmt = stmt.where(FactRow.unit == unit)
    if form:
        stmt = stmt.where(Filing.form == form)
    facts = [Fact.from_row(r) for r in session.execute(stmt)]

    if as_reported:
        facts.sort(key=lambda f: (f.unit, f.period_end, f.period_start, f.filed or date.min))
    else:
        facts = latest_per_period(facts)
        if fill_q4:
            facts = derive_q4(facts)
    if split_adjusted:
        splits = session.execute(
            select(CorporateAction.ex_date, CorporateAction.value)
            .where(CorporateAction.security_id == security.id, CorporateAction.action == "split")
            .distinct()
        ).all()
        facts = split_adjust(facts, [tuple(s) for s in splits])
    if period_type:
        facts = [f for f in facts if f.period_type == period_type]
    return facts


@router.get("/fundamentals/{ticker}/statements")
def financial_statements(
    session: SessionDep,
    ticker: str,
    statement: str = Query("income", pattern="^(income|balance|cash_flow)$"),
    period: str = Query("annual", pattern="^(annual|quarter)$"),
    limit: int = Query(8, le=40),
) -> dict:
    """Standard statement lines per period, newest first. Balance-sheet lines are taken at
    each period's end date; `concepts` names the XBRL concept behind each line."""
    from fin_intel.statements import LINE_ITEMS

    cik = issuer_cik_or_404(session, ticker)
    lines = [name for name, (stmt, _, _) in LINE_ITEMS.items() if stmt == statement]
    rows = session.execute(
        select(StatementItem, Concept.taxonomy, Concept.name)
        .join(Concept, Concept.id == StatementItem.concept_id)
        .where(StatementItem.cik == cik, StatementItem.line_item.in_(lines))
    ).all()
    periods: dict[tuple, dict] = {}
    for item, taxonomy, name in rows:
        if statement == "balance":
            # Balances are instants; annual means those at a fiscal year end.
            if item.period_type != "instant" or (period == "annual" and item.fiscal_period != "FY"):
                continue
        elif item.period_type != period:
            continue
        entry = periods.setdefault(
            (item.period_start, item.period_end),
            {
                "period_start": item.period_start,
                "period_end": item.period_end,
                "fiscal_year": item.fiscal_year,
                "fiscal_period": item.fiscal_period,
                "concepts": {},
            },
        )
        entry[item.line_item] = item.value
        entry["concepts"][item.line_item] = f"{taxonomy}:{name}"
    ordered = sorted(periods.values(), key=lambda e: e["period_end"], reverse=True)[:limit]
    return {"ticker": ticker.upper(), "statement": statement, "period": period, "periods": ordered}


@router.get("/metrics/{ticker}")
def company_metrics(session: SessionDep, ticker: str, history: int = Query(0, le=365)) -> dict:
    """Latest valuation, quality, growth and score metrics, plus up to `history` earlier
    daily rows (each computed from what was known that day)."""
    from fin_intel.screener import NUMERIC

    security = security_or_404(session, ticker)
    rows = list(
        session.scalars(
            select(CompanyMetrics)
            .where(CompanyMetrics.security_id == security.id)
            .order_by(CompanyMetrics.as_of.desc())
            .limit(history + 1)
        )
    )
    if not rows:
        raise HTTPException(404, f"no metrics for {ticker} (not a primary listed security?)")

    def as_dict(m: CompanyMetrics) -> dict:
        return {"as_of": m.as_of, "period_end": m.period_end, **{c: getattr(m, c) for c in NUMERIC}}

    return {
        "ticker": ticker.upper(),
        "latest": as_dict(rows[0]),
        "history": [as_dict(m) for m in rows[1:]],
    }


@router.get("/screener")
def screener_endpoint(
    session: SessionDep,
    where: Annotated[list[str] | None, Query(description="e.g. pe<15, roic>=0.15")] = None,
    sort: str | None = Query(None, description="metric, '-' prefix for descending"),
    rank: str | None = Query(None, description="'magic': earnings yield + ROIC ranks"),
    preset: str | None = Query(None, description="magic, deep_value, quality, cash_cows"),
    limit: int = Query(50, le=500),
    sector: Annotated[list[str] | None, Query(description="only these sectors")] = None,
    exclude_sector: Annotated[list[str] | None, Query(description="e.g. finance")] = None,
) -> dict:
    """Companies matching every filter on their latest metrics. Sectors come from SIC
    codes: agriculture, mining, construction, manufacturing, transportation, utilities,
    wholesale, retail, finance, services, public."""
    try:
        as_of, rows = screener.screen(
            session, where, sort, rank, preset, limit, sector, exclude_sector
        )
    except screener.ScreenError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"as_of": as_of, "count": len(rows), "results": rows}


@router.get("/filings/search")
def filing_search(
    session: SessionDep,
    q: str,
    ticker: str | None = None,
    since: date | None = None,
    limit: int = Query(20, le=200),
) -> list[filing_text.Hit]:
    """Full-text search over 10-K/10-Q risk factors and MD&A and earnings press releases
    (web-search syntax: words, "phrases", OR, -exclude)."""
    ciks = None
    if ticker:
        security = symbols.get_security(session, ticker)
        if security is None or not security.cik:
            raise HTTPException(404, f"unknown ticker {ticker}")
        ciks = [security.cik]
    return filing_text.search(session, q, ciks, since, limit)


@router.get("/filings/{accession}/text")
def filing_sections(session: SessionDep, accession: str, section: str | None = None) -> list[dict]:
    """A filing's stored sections as plain text (section: risk_factors, mdna, EX-99.1...)."""
    rows = filing_text.get(session, accession, section)
    if not rows:
        raise HTTPException(404, f"no text for {accession}")
    return [
        {"section": r.section, "form": r.form, "filed": r.filed, "period": r.period, "text": r.text}
        for r in rows
    ]
