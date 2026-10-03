import io
import zipfile
from datetime import date

import respx
from sqlalchemy import func, select

from fin_intel import ingest, insiders
from fin_intel.models import InsiderTransaction
from fin_intel.providers import SecProvider
from fin_intel.providers.sec import parse_daily_index
from fin_intel.rebuild import rebuild


def tsv(header, rows):
    return "\t".join(header) + "\n" + "".join("\t".join(r) + "\n" for r in rows)


def dataset(transactions):
    """A quarterly data set zip. transactions: (accession, owner_cik, owner, code, shares,
    price, trans_date, plan)"""
    subs, owners, trans = [], [], []
    for i, (acc, cik, owner, code, shares, price, day, plan) in enumerate(transactions):
        subs.append(
            [acc, "30-JUN-2026", "4", "0000028917", "DILLARD'S, INC.", "DDS", "1" if plan else "0"]
        )
        owners.append([acc, cik, owner, "Director", ""])
        trans.append(
            [
                acc,
                str(9000 + i),
                "Common Stock",
                day,
                "4",
                code,
                shares,
                price,
                "A" if code == "P" else "D",
                "1000.0",
                "D",
            ]
        )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "SUBMISSION.tsv",
            tsv(
                [
                    "ACCESSION_NUMBER",
                    "FILING_DATE",
                    "DOCUMENT_TYPE",
                    "ISSUERCIK",
                    "ISSUERNAME",
                    "ISSUERTRADINGSYMBOL",
                    "AFF10B5ONE",
                ],
                subs,
            ),
        )
        z.writestr(
            "REPORTINGOWNER.tsv",
            tsv(
                [
                    "ACCESSION_NUMBER",
                    "RPTOWNERCIK",
                    "RPTOWNERNAME",
                    "RPTOWNER_RELATIONSHIP",
                    "RPTOWNER_TITLE",
                ],
                owners,
            ),
        )
        z.writestr(
            "NONDERIV_TRANS.tsv",
            tsv(
                [
                    "ACCESSION_NUMBER",
                    "NONDERIV_TRANS_SK",
                    "SECURITY_TITLE",
                    "TRANS_DATE",
                    "TRANS_FORM_TYPE",
                    "TRANS_CODE",
                    "TRANS_SHARES",
                    "TRANS_PRICEPERSHARE",
                    "TRANS_ACQUIRED_DISP_CD",
                    "SHRS_OWND_FOLWNG_TRANS",
                    "DIRECT_INDIRECT_OWNERSHIP",
                ],
                trans,
            ),
        )
    return buf.getvalue()


def form4(
    owner_cik="0001111111",
    owner="DOE JANE",
    code="P",
    shares="500",
    price="20.5",
    day="2026-06-29",
    filed="20260630",
):
    return f"""<SEC-DOCUMENT>
FILED AS OF DATE:\t\t{filed}
<XML>
<?xml version="1.0"?>
<ownershipDocument>
  <documentType>4</documentType>
  <issuer><issuerCik>0000028917</issuerCik><issuerTradingSymbol>dds</issuerTradingSymbol></issuer>
  <reportingOwner>
    <reportingOwnerId><rptOwnerCik>{owner_cik}</rptOwnerCik><rptOwnerName>{owner}</rptOwnerName></reportingOwnerId>
    <reportingOwnerRelationship><isDirector>1</isDirector></reportingOwnerRelationship>
  </reportingOwner>
  <aff10b5One>0</aff10b5One>
  <nonDerivativeTable><nonDerivativeTransaction>
    <securityTitle><value>Common Stock</value></securityTitle>
    <transactionDate><value>{day}</value></transactionDate>
    <transactionCoding><transactionCode>{code}</transactionCode></transactionCoding>
    <transactionAmounts>
      <transactionShares><value>{shares}</value></transactionShares>
      <transactionPricePerShare><value>{price}</value></transactionPricePerShare>
      <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
    </transactionAmounts>
    <postTransactionAmounts><sharesOwnedFollowingTransaction><value>1000</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
    <ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
  </nonDerivativeTransaction></nonDerivativeTable>
</ownershipDocument>
</XML>
</SEC-DOCUMENT>""".encode()


def count(session):
    return session.scalar(select(func.count()).select_from(InsiderTransaction))


def test_dataset_and_form4_produce_the_same_row(session):
    rows = insiders.parse_dataset(
        dataset(
            [
                (
                    "0001-26-000001",
                    "0001111111",
                    "DOE JANE",
                    "P",
                    "500.0",
                    "20.5",
                    "29-JUN-2026",
                    False,
                )
            ]
        )
    )
    (row,) = rows
    assert (row["issuer_symbol"], row["trans_code"], row["shares"], row["trans_date"]) == (
        "DDS",
        "P",
        500.0,
        date(2026, 6, 29),
    )
    insiders.load(session, rows)
    # The same filing's XML, as fetched from the daily index, is recognized as the same row.
    ingest.load_form4(session, "0001-26-000001", form4())
    assert count(session) == 1
    stored = session.scalar(select(InsiderTransaction))
    assert stored.filing_date == date(2026, 6, 30) and stored.issuer_symbol == "DDS"


def test_cluster_buys(session):
    insiders.load(
        session,
        insiders.parse_dataset(
            dataset(
                [
                    ("A1", "1", "ONE", "P", "100", "10", "10-JUN-2026", False),
                    ("A2", "2", "TWO", "P", "200", "10", "12-JUN-2026", False),
                    ("A3", "3", "THREE", "P", "300", "11", "20-JUN-2026", False),
                    (
                        "A4",
                        "4",
                        "FOUR",
                        "P",
                        "999",
                        "11",
                        "21-JUN-2026",
                        True,
                    ),  # 10b5-1 plan: excluded
                    ("A5", "5", "FIVE", "S", "999", "11", "21-JUN-2026", False),  # a sale: excluded
                ]
            )
        ),
    )
    (cluster,) = insiders.cluster_buys(session, days=30, min_insiders=3, as_of=date(2026, 6, 30))
    assert (cluster.symbol, cluster.insiders, cluster.shares) == ("DDS", 3, 600)
    assert cluster.value == 100 * 10 + 200 * 10 + 300 * 11
    assert insiders.cluster_buys(session, days=30, min_insiders=4, as_of=date(2026, 6, 30)) == []


def test_parse_daily_index():
    text = """Description:  Daily Index of EDGAR Dissemination Feed by Form Type
Form Type   Company Name      CIK         Date Filed  File Name
---------------------------------------------------------------------------------
4           DILLARDS INC      28917       20260630    edgar/data/28917/0001-26-000001.txt
4           DOE JANE          1111111     20260630    edgar/data/28917/0001-26-000001.txt
10-K        APPLE INC         320193      20260630    edgar/data/320193/0000320193-26-000123.txt
"""
    assert parse_daily_index(text) == [
        ("4", "0001-26-000001", "edgar/data/28917/0001-26-000001.txt"),
        ("10-K", "0000320193-26-000123", "edgar/data/320193/0000320193-26-000123.txt"),
    ]


@respx.mock
def test_sync_day_skips_loaded_filings_and_rebuilds(session, raw_store):
    index = """Form Type   Company Name      CIK         Date Filed  File Name
---------------------------------------------------------------------------------
4           DILLARDS INC      28917       20260630    edgar/data/28917/0001-26-000001.txt
4           DILLARDS INC      28917       20260630    edgar/data/28917/0001-26-000002.txt
"""
    respx.get("https://www.sec.gov/Archives/edgar/daily-index/2026/QTR2/form.20260630.idx").respond(
        text=index
    )
    first = respx.get("https://www.sec.gov/Archives/edgar/data/28917/0001-26-000001.txt").respond(
        content=form4()
    )
    second = respx.get("https://www.sec.gov/Archives/edgar/data/28917/0001-26-000002.txt").respond(
        content=form4(owner_cik="0002222222", owner="ROE JOHN", shares="50")
    )
    sec = SecProvider(raw_store=raw_store)
    insiders.load(
        session,
        insiders.parse_dataset(
            dataset(
                [
                    (
                        "0001-26-000001",
                        "0001111111",
                        "DOE JANE",
                        "P",
                        "500.0",
                        "20.5",
                        "29-JUN-2026",
                        False,
                    )
                ]
            )
        ),
    )
    session.commit()

    assert ingest.sync_insider_day(session, sec, date(2026, 6, 30)) == 1
    assert not first.called and second.called  # already loaded from the data set
    assert count(session) == 2
    with respx.mock:
        rebuild(session, raw_store, "insiders")
    assert count(session) == 1  # only the fetched Form 4 is in raw; the data set wasn't


def test_holiday_index_is_empty(session, raw_store):
    with respx.mock:
        respx.get(
            "https://www.sec.gov/Archives/edgar/daily-index/2026/QTR3/form.20260907.idx"
        ).respond(404)
        assert (
            ingest.sync_insider_day(session, SecProvider(raw_store=raw_store), date(2026, 9, 7))
            == 0
        )
