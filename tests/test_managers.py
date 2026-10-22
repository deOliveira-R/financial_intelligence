from datetime import date

import pytest
from sqlalchemy import select

from fin_intel import managers
from fin_intel.models import (
    InstitutionalFiler,
    InstitutionalPosition,
    ManagerBestIdea,
    ManagerQuarter,
)

Q3, Q4 = date(2016, 9, 30), date(2016, 12, 31)


def position(filer, period, cusip, value, shares, put_call="", share_type="SH"):
    return InstitutionalPosition(
        filer_cik=filer,
        period=period,
        cusip=cusip,
        put_call=put_call,
        shares=shares,
        share_type=share_type,
        value=value,
        accession=f"{filer}-{period}",
        filed=period,
    )


def test_quarter_before():
    assert managers.quarter_before(date(2016, 12, 31)) == date(2016, 9, 30)
    assert managers.quarter_before(date(2017, 3, 31)) == date(2016, 12, 31)
    assert managers.quarter_before(date(2017, 6, 30)) == date(2017, 3, 31)


def test_books_turnover_and_best_ideas(session):
    session.add_all(
        [InstitutionalFiler(cik=1, name="Patient"), InstitutionalFiler(cik=2, name="New")]
    )
    rows = []
    # Filer 1 held A, B, C, D in Q3; in Q4 sold D, kept A and B, doubled C, opened E (small).
    for cusip, value, shares in (("A", 500, 10), ("B", 400, 10), ("C", 100, 10), ("D", 50, 10)):
        rows.append(position(1, Q3, cusip, value, shares))
    for cusip, value, shares in (("A", 520, 10), ("B", 390, 10), ("C", 210, 20), ("E", 30, 5)):
        rows.append(position(1, Q4, cusip, value, shares))
    rows.append(position(1, Q4, "A", 999, 1, put_call="PUT"))  # options don't count
    rows.append(position(1, Q4, "N", 999, 1, share_type="PRN"))  # nor principal amounts
    # Filer 2 first reports in Q4: no turnover, every position new.
    rows += [position(2, Q4, "A", 100, 1), position(2, Q4, "B", 90, 1)]
    session.add_all(rows)
    session.commit()

    assert managers.periods_to_do(session) == [Q3, Q4]
    assert managers.compute(session) == 3  # filer 1 twice, filer 2 once
    books = {(b.filer_cik, b.period): b for b in session.scalars(select(ManagerQuarter))}
    q4 = books[1, Q4]
    assert q4.positions == 4 and q4.value == 1150.0
    assert q4.previous_positions == 4 and q4.turnover == pytest.approx(0.25)  # D of A, B, C, D
    assert books[1, Q3].turnover is None and books[1, Q3].previous_positions is None
    assert books[2, Q4].turnover is None

    ideas = {(i.filer_cik, i.period, i.cusip): i for i in session.scalars(select(ManagerBestIdea))}
    assert set(ideas) == {
        (1, Q3, "A"), (1, Q3, "B"), (1, Q3, "C"), (1, Q3, "D"),  # first quarter: all new
        (1, Q4, "C"), (1, Q4, "E"),  # doubled and opened; A and B unchanged
        (2, Q4, "A"), (2, Q4, "B"),
    }  # fmt: skip
    c = ideas[1, Q4, "C"]
    assert c.rank == 3 and c.previous_shares == 10.0 and c.shares == 20.0  # A, B, then C
    assert ideas[1, Q4, "E"].previous_shares is None
    # Recomputing the latest two quarters is idempotent.
    assert managers.compute(session) == 3
    c_again = session.get(ManagerBestIdea, (1, Q4, "C"))
    assert c_again is not None and c_again.rank == 3
