from datetime import date

import pytest
from sqlalchemy import select

from fin_intel import quarterly
from fin_intel.models import (
    CusipMapping,
    InsiderTransaction,
    InstitutionalFiler,
    InstitutionalPosition,
    Issuer,
    QuarterlyPrice,
    Security,
)

AAPL, CELG = "037833100", "151020104"
Q4, Q1 = date(2022, 12, 31), date(2023, 3, 31)


def insider_buy(key, cik, symbol, day):
    return InsiderTransaction(
        key=key,
        accession=key,
        issuer_cik=cik,
        issuer_symbol=symbol,
        trans_date=day,
        filing_date=day,
        trans_code="P",
    )


def position(filer, period, cusip, value, shares, put_call="", share_type="SH"):
    return InstitutionalPosition(
        filer_cik=filer,
        period=period,
        cusip=cusip,
        put_call=put_call,
        shares=shares,
        share_type=share_type,
        value=value,
        accession=f"{filer}-{period}-{cusip}{put_call}{share_type}",
        filed=period,
    )


def test_median_of_value_per_share_with_mapping_and_recent_periods(session):
    session.add(Issuer(cik=320193, name="Apple"))
    session.flush()
    apple = Security(ticker="AAPL", cik=320193, origin="sec")
    session.add(apple)
    session.flush()
    session.add(CusipMapping(cusip=AAPL, security_id=apple.id, ticker="AAPL"))
    session.add_all([InstitutionalFiler(cik=f, name=f"F{f}") for f in (1, 2, 3)])
    session.add_all(
        [
            position(1, Q4, AAPL, 1000.0, 10.0),  # 100
            position(2, Q4, AAPL, 2200.0, 20.0),  # 110
            position(3, Q4, AAPL, 999.0, 1.0, put_call="PUT"),  # options: ignored
            position(3, Q4, AAPL, 999.0, 1.0, share_type="PRN"),  # principal: ignored
            position(1, Q4, "UNMAPPED1", 500.0, 5.0),
            position(1, Q1, AAPL, 1500.0, 10.0),
            position(1, Q4, CELG, 900.0, 10.0),  # a company dead before our listings begin
        ]
    )
    # Celgene maps to a US ticker but no security; insiders filed under CELG around then.
    session.add(CusipMapping(cusip=CELG, ticker="CELG", security_type="Common Stock"))
    session.add_all(
        [
            insider_buy("c1", 816284, "CELG", date(2022, 8, 1)),
            insider_buy("c2", 816284, "CELG", date(2023, 2, 1)),
            insider_buy("c3", 999, "CELG", date(2015, 1, 1)),  # another issuer, outside the window
        ]
    )
    session.commit()
    assert quarterly.periods_to_do(session) == [Q4, Q1]
    assert quarterly.compute(session) == 4
    rows = {(r.cusip, r.period): r for r in session.scalars(select(QuarterlyPrice))}
    apple_q4 = rows[(AAPL, Q4)]
    assert apple_q4.price == pytest.approx(105.0)
    assert apple_q4.filers == 2 and apple_q4.value_total == 3200.0 and apple_q4.shares_total == 30.0
    assert apple_q4.security_id == apple.id and apple_q4.cik == 320193
    assert apple_q4.cik_source == "security"
    assert rows[("UNMAPPED1", Q4)].security_id is None and rows[("UNMAPPED1", Q4)].cik is None
    celgene = rows[(CELG, Q4)]
    assert celgene.security_id is None and celgene.cik == 816284
    assert celgene.cik_source == "insiders"
    assert rows[(AAPL, Q1)].price == 150.0
    # Both quarters are the latest two, so both are recomputed next time; a third would not be.
    assert quarterly.periods_to_do(session) == [Q4, Q1]
    session.add(position(1, date(2023, 6, 30), AAPL, 1800.0, 10.0))
    session.commit()
    quarterly.compute(session)
    assert quarterly.periods_to_do(session) == [Q1, date(2023, 6, 30)]
