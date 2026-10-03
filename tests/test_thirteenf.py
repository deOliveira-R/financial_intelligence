import io
import json
import zipfile
from datetime import date

import httpx
import respx
from sqlalchemy import func, select

from fin_intel import ingest, thirteenf
from fin_intel.models import CusipMapping, InstitutionalPosition, Security
from fin_intel.providers import OpenFigiProvider, SecProvider
from fin_intel.rebuild import rebuild

AAPL, KO, BOND = "037833100", "191216100", "912828XX1"


def tsv(header, rows):
    return "\t".join(header) + "\n" + "".join("\t".join(map(str, r)) + "\n" for r in rows)


def dataset(filings, holdings):
    """filings: (accession, cik, type, period, filed, amendment, name);
    holdings: (accession, sk, issuer, cusip, value, shares, putcall, figi)"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "SUBMISSION.tsv",
            tsv(
                ["ACCESSION_NUMBER", "FILING_DATE", "SUBMISSIONTYPE", "CIK", "PERIODOFREPORT"],
                [
                    (a, filed, t, f"{cik:010d}", period)
                    for a, cik, t, period, filed, _, _ in filings
                ],
            ),
        )
        z.writestr(
            "COVERPAGE.tsv",
            tsv(
                ["ACCESSION_NUMBER", "AMENDMENTTYPE", "FILINGMANAGER_NAME"],
                [(a, amend, name) for a, _, _, _, _, amend, name in filings],
            ),
        )
        z.writestr(
            "INFOTABLE.tsv",
            tsv(
                [
                    "ACCESSION_NUMBER",
                    "INFOTABLE_SK",
                    "NAMEOFISSUER",
                    "TITLEOFCLASS",
                    "CUSIP",
                    "FIGI",
                    "VALUE",
                    "SSHPRNAMT",
                    "SSHPRNAMTTYPE",
                    "PUTCALL",
                ],
                [
                    (a, sk, issuer, "COM", cusip, figi, value, shares, "SH", pc)
                    for a, sk, issuer, cusip, value, shares, pc, figi in holdings
                ],
            ),
        )
    return buf.getvalue()


BRK = (1067983, "BERKSHIRE HATHAWAY INC")
Q1 = [("B1", *BRK[:1], "13F-HR", "31-MAR-2026", "15-MAY-2026", "", BRK[1])]
Q1_ROWS = [
    ("B1", 1, "APPLE INC", AAPL, 2_000, 10, "", ""),
    ("B1", 2, "APPLE INC", AAPL, 3_000, 15, "", ""),  # same security, another sub-manager
    ("B1", 3, "COCA COLA CO", KO, 800, 10, "", "BBG000BMX289"),
    ("B1", 4, "APPLE INC", AAPL, 100, 1, "PUT", ""),  # options are separate positions
]


def positions(session):
    return {
        (p.period, p.cusip, p.put_call): (p.shares, p.value)
        for p in session.scalars(select(InstitutionalPosition))
    }


def test_rows_are_summed_per_position(session):
    thirteenf.load(session, dataset(Q1, Q1_ROWS))
    got = positions(session)
    assert got[(date(2026, 3, 31), AAPL, "")] == (25, 5_000)
    assert got[(date(2026, 3, 31), AAPL, "PUT")] == (1, 100)
    # The FIGI a filer reported is kept as a free mapping hint.
    assert session.get(CusipMapping, KO).figi == "BBG000BMX289"


def test_amendments(session):
    filings = Q1 + [
        ("B2", BRK[0], "13F-HR/A", "31-MAR-2026", "20-MAY-2026", "NEW HOLDINGS", BRK[1]),
        ("B3", BRK[0], "13F-HR/A", "31-MAR-2026", "25-MAY-2026", "RESTATEMENT", BRK[1]),
    ]
    rows = Q1_ROWS + [("B2", 5, "TREASURY NOTE", BOND, 50, 50, "", "")]
    thirteenf.load(session, dataset(filings[:2], rows))
    assert (date(2026, 3, 31), BOND, "") in positions(session)  # added
    thirteenf.load(
        session, dataset(filings, rows + [("B3", 6, "APPLE INC", AAPL, 9_000, 30, "", "")])
    )
    assert positions(session) == {(date(2026, 3, 31), AAPL, ""): (30, 9_000)}  # replaced


def test_manager_changes(session):
    q2 = [("C1", BRK[0], "13F-HR", "30-JUN-2026", "14-AUG-2026", "", BRK[1])]
    q2_rows = [
        ("C1", 1, "APPLE INC", AAPL, 2_000, 20, "", ""),
        ("C1", 2, "NEW CO", BOND, 10, 10, "", ""),
    ]
    thirteenf.load(session, dataset(Q1 + q2, Q1_ROWS + q2_rows))
    period, changes = thirteenf.manager_changes(session, BRK[0])
    assert period == date(2026, 6, 30)
    kinds = {(c.cusip, c.put_call): c.change for c in changes}
    assert kinds == {
        (AAPL, ""): "reduced",
        (BOND, ""): "new",
        (KO, ""): "sold",
        (AAPL, "PUT"): "sold",
    }
    assert [f.name for f in thirteenf.find_filers(session, "berkshire")] == [BRK[1]]


@respx.mock
def test_openfigi_mapping_links_securities_and_rebuilds(session, raw_store):
    session.add(Security(ticker="AAPL", figi="BBG000B9XRY4", origin="sec"))
    session.commit()
    answers = {
        AAPL: {
            "data": [
                {
                    "figi": "BBG000B9Y5X2",
                    "compositeFIGI": "BBG000B9XRY4",
                    "ticker": "AAPL",
                    "exchCode": "US",
                    "securityType": "Common Stock",
                }
            ]
        },
        BOND: {"warning": "No identifier found."},
    }

    def openfigi(request):  # answers in request order, as OpenFIGI does
        jobs = json.loads(request.content)
        return httpx.Response(200, json=[answers[j["idValue"]] for j in jobs])

    route = respx.post("https://api.openfigi.com/v3/mapping").mock(side_effect=openfigi)
    respx.get("https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets").respond(
        text='<a href="/files/x/form-13f-data-sets/01mar2026-31may2026_form13f.zip">Q</a>'
    )
    respx.get(
        "https://www.sec.gov/files/x/form-13f-data-sets/01mar2026-31may2026_form13f.zip"
    ).respond(
        content=dataset(
            Q1, [r for r in Q1_ROWS if r[3] != KO] + [("B1", 9, "TREASURY", BOND, 1, 1, "", "")]
        )
    )
    sec = SecProvider(raw_store=raw_store)
    ((period, url),) = sec.list_13f_datasets().items()
    assert period == "2026-03-01_2026-05-31"
    ingest.sync_13f_dataset(session, sec, period, url)
    assert sorted(thirteenf.unmapped_cusips(session)) == sorted([AAPL, BOND])

    ingest.sync_cusip_mappings(session, OpenFigiProvider(raw_store=raw_store))
    sent = json.loads(route.calls[0].request.content)
    assert {j["idValue"] for j in sent} == {AAPL, BOND}
    aapl = session.get(CusipMapping, AAPL)
    assert aapl.security_id == session.scalar(select(Security.id).where(Security.ticker == "AAPL"))
    assert session.get(CusipMapping, BOND).security_type == "unknown"
    assert thirteenf.unmapped_cusips(session) == []  # nothing left to ask

    before = session.scalar(select(func.count()).select_from(InstitutionalPosition))
    with respx.mock:
        rebuild(session, raw_store, "holdings")
    assert session.scalar(select(func.count()).select_from(InstitutionalPosition)) == before
    assert session.get(CusipMapping, AAPL).security_id is not None  # re-linked from raw
