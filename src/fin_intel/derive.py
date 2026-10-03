"""Derived layer: our inferences on top of reported data. Re-runnable at any time, offline.

For each issuer: infer the fiscal calendar (periods.py), store it, label every fact with
its own period type and fiscal year/period, find each filing's primary period end, and
build standard statement lines (statements.py).
"""

from sqlalchemy import bindparam, delete, select, update
from sqlalchemy.orm import Session

from fin_intel import statements
from fin_intel.models import Concept, Fact, Filing, FiscalCalendar, Issuer
from fin_intel.periods import FiscalSchedule, label


def derive_issuer(session: Session, cik: int) -> None:
    rows = session.execute(
        select(
            Fact.filing_id,
            Fact.concept_id,
            Fact.unit,
            Fact.period_start,
            Fact.period_end,
            Fact.instant,
            Concept.taxonomy,
            Filing.form,
            Filing.filed,
            Filing.fiscal_year,
            Filing.fiscal_period,
        )
        .join(Filing, Filing.id == Fact.filing_id)
        .join(Concept, Concept.id == Fact.concept_id)
        .where(Fact.cik == cik)
    ).all()
    facts = [
        {
            "filing_id": r.filing_id,
            "concept_id": r.concept_id,
            "unit": r.unit,
            "period_start": r.period_start,
            "period_end": r.period_end,
            "instant": r.instant,
            "taxonomy": r.taxonomy,
            "accession": r.filing_id,
            "form": r.form,
            "filed": r.filed,
            "filing_fiscal_year": r.fiscal_year,
            "filing_fiscal_period": r.fiscal_period,
        }
        for r in rows
    ]

    schedule = FiscalSchedule.from_facts(facts)
    session.execute(delete(FiscalCalendar).where(FiscalCalendar.cik == cik))
    if schedule:
        session.add_all(
            FiscalCalendar(
                cik=cik,
                segment=i,
                year_end_month=seg.calendar.month,
                year_end_day=seg.calendar.day,
                year_offset=seg.calendar.year_offset,
                first_year_end=seg.ends[0][0],
                last_year_end=seg.last_end,
            )
            for i, seg in enumerate(schedule.segments)
        )

    label(facts, schedule)
    # One compiled UPDATE executed for all facts (the ORM's bulk update spent most of the
    # time on per-row bookkeeping).
    facts_table = Fact.__table__
    pk = ("filing_id", "concept_id", "unit", "period_start", "period_end")
    session.connection().execute(
        update(facts_table)
        .where(*(facts_table.c[c] == bindparam(f"k_{c}") for c in pk))
        .values(
            period_type=bindparam("v_period_type"),
            fiscal_year=bindparam("v_fiscal_year"),
            fiscal_period=bindparam("v_fiscal_period"),
        ),
        [
            {
                **{f"k_{c}": f[c] for c in pk},
                "v_period_type": f["period_type"],
                "v_fiscal_year": f["fiscal_year"],
                "v_fiscal_period": f["fiscal_period"],
            }
            for f in facts
        ],
    )

    # A filing's primary period: its latest duration end on or before the filing date
    # (cover-page instants and forward-looking facts are dated later).
    report_ends: dict[int, object] = {}
    for f in facts:
        if f["instant"] or f["taxonomy"] == "dei":
            continue
        if f["filed"] and f["period_end"] > f["filed"]:
            continue
        if f["period_end"] > report_ends.get(f["filing_id"], f["period_end"].min):
            report_ends[f["filing_id"]] = f["period_end"]
    if report_ends:
        session.execute(
            update(Filing),
            [{"id": i, "report_period_end": end} for i, end in report_ends.items()],
        )
    session.flush()
    statements.build_issuer(session, cik)


def derive_all(session: Session) -> int:
    ciks = session.scalars(select(Issuer.cik).where(Issuer.cik.in_(select(Fact.cik)))).all()
    for cik in ciks:
        derive_issuer(session, cik)
    session.commit()
    return len(ciks)
