from datetime import date

import pytest
from sqlalchemy import select
from test_thirteenf import dataset

from fin_intel import quarterly
from fin_intel.models import (
    CusipMapping,
    InsiderTransaction,
    Issuer,
    QuarterlyPrice,
    QuarterlyPricePart,
    Security,
)

AAPL, CELG = "037833100", "151020104"
Q4, Q1 = date(2022, 12, 31), date(2023, 3, 31)
FILE = "2022-10-01_2023-05-31"
# (accession, cik, type, period, filed, amendment, name): filers 1 and 2 in the main file,
# filed after and before the switch from thousands to dollars.
FILINGS = [
    ("F1", 1, "13F-HR", "31-DEC-2022", "10-JAN-2023", "", "One"),
    ("F2", 2, "13F-HR", "31-DEC-2022", "02-JAN-2023", "", "Two"),  # in thousands
    ("F3", 3, "13F-NT", "31-DEC-2022", "10-JAN-2023", "", "Notice"),  # no holdings
    ("F4", 1, "13F-HR", "31-MAR-2023", "10-MAY-2023", "", "One"),
]
# (accession, sk, issuer, cusip, value, shares, putcall, figi)
HOLDINGS = [
    ("F1", 1, "APPLE", AAPL, 1000, 10, "", ""),  # 100
    ("F1", 2, "APPLE", AAPL, 100, 1, "PUT", ""),  # options: ignored
    ("F2", 1, "APPLE", AAPL, 1.1, 10, "", ""),  # 1,100 dollars -> 110
    ("F2", 2, "CELGENE", CELG, 0.9, 10, "", ""),  # 90
    ("F3", 1, "APPLE", AAPL, 999, 1, "", ""),  # a notice: ignored
    ("F4", 1, "APPLE", AAPL, 1500, 10, "", ""),  # 150
]


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


def test_parts_from_a_file_then_prices_with_mapping_and_bridge(session):
    session.add(Issuer(cik=320193, name="Apple"))
    session.flush()
    apple = Security(ticker="AAPL", cik=320193, origin="sec")
    session.add(apple)
    session.flush()
    session.add(CusipMapping(cusip=AAPL, security_id=apple.id, ticker="AAPL"))
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

    parts = quarterly.parse_parts(FILE, dataset(FILINGS, HOLDINGS))
    by_key = {(p["cusip"], p["period"]): p for p in parts}
    assert by_key[(AAPL, Q4)]["price"] == pytest.approx(105.0)  # median of 100 and 110
    assert by_key[(AAPL, Q4)]["filers"] == 2
    assert by_key[(AAPL, Q4)]["value_total"] == pytest.approx(2100.0)
    assert by_key[(CELG, Q4)] == {
        "file_key": FILE,
        "cusip": CELG,
        "period": Q4,
        "price": 90.0,
        "filers": 1,
        "value_total": 900.0,
        "shares_total": 10.0,
    }
    assert (AAPL, Q1) in by_key and len(parts) == 3

    assert quarterly.load_parts(session, FILE, dataset(FILINGS, HOLDINGS)) == 3
    session.commit()
    assert quarterly.periods_to_do(session) == [Q4, Q1]
    assert quarterly.compute(session) == 3
    rows = {(r.cusip, r.period): r for r in session.scalars(select(QuarterlyPrice))}
    apple_q4 = rows[(AAPL, Q4)]
    assert apple_q4.price == pytest.approx(105.0) and apple_q4.filers == 2
    assert apple_q4.security_id == apple.id and apple_q4.cik == 320193
    assert apple_q4.cik_source == "security"
    celgene = rows[(CELG, Q4)]
    assert celgene.security_id is None and celgene.cik == 816284
    assert celgene.cik_source == "insiders"
    assert rows[(AAPL, Q1)].price == 150.0

    # A later file with more reports for the quarter wins.
    more = [
        ("A1", 5, "13F-HR", "31-DEC-2022", "20-FEB-2023", "", "Five"),
        ("A2", 6, "13F-HR", "31-DEC-2022", "21-FEB-2023", "", "Six"),
        ("A3", 7, "13F-HR", "31-DEC-2022", "22-FEB-2023", "", "Seven"),
    ]
    rows_more = [(a, 1, "APPLE", AAPL, 1200, 10, "", "") for a, *_ in more]  # 120 each
    quarterly.load_parts(session, "2023-01-01_2023-03-31", dataset(more, rows_more))
    session.commit()
    quarterly.compute(session, [Q4])
    apple_q4 = session.get(QuarterlyPrice, (AAPL, Q4))
    assert apple_q4.price == 120.0 and apple_q4.filers == 3
    first = session.get(QuarterlyPricePart, (FILE, AAPL, Q4))
    assert first.filers == 2  # the earlier file's part is kept, not chosen
    assert quarterly.periods_to_do(session) == [Q4, Q1]  # the latest two are always redone
