import io
import json
import zipfile
from datetime import date
from pathlib import Path

import httpx
import respx
from sqlalchemy import func, select

from fin_intel import congress, ingest
from fin_intel.models import CongressReport, CongressTrade
from fin_intel.providers import HouseProvider, SenateProvider
from fin_intel.rebuild import rebuild

FIXTURES = Path(__file__).parent / "fixtures"
PELOSI = (FIXTURES / "20034836.pdf").read_bytes()  # two option purchases, one page
WALBERG = (FIXTURES / "20034660.pdf").read_bytes()  # 16 rows, one cut by a page break

HOUSE = "https://disclosures-clerk.house.gov/public_disc"
SENATE = "https://efdsearch.senate.gov"


def house_index(*members):
    """A yearly index zip. members: (DocID, FilingType, Last, FilingDate)"""
    rows = "".join(
        f"<Member><Prefix>Hon.</Prefix><Last>{last}</Last><First>Test</First><Suffix/>"
        f"<FilingType>{kind}</FilingType><StateDst>CA11</StateDst><Year>2026</Year>"
        f"<FilingDate>{filed}</FilingDate><DocID>{doc}</DocID></Member>"
        for doc, kind, last, filed in members
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "2026FD.xml", f'<?xml version="1.0"?><FinancialDisclosure>{rows}</FinancialDisclosure>'
        )
        z.writestr("2026FD.txt", "")
    return buf.getvalue()


def senate_row(n, when, owner, ticker, asset, kind, amount, comment="--"):
    link = f'<a href="https://finance.yahoo.com/quote/{ticker}" target="_blank">{ticker}</a>'
    return (
        f"<tr><td>{n}</td><td> {when} </td><td>{owner}</td><td> {link} </td>"
        f"<td> {asset} </td><td>Stock</td><td>{kind}</td><td>{amount}</td><td>{comment}</td></tr>"
    )


SENATE_PTR = f"""<html><body>
<h1 class="mb-2">Periodic Transaction Report for 10/01/2026</h1>
<h2 class="filedReport">
    The Honorable Sheldon
    Whitehouse

    (Whitehouse, Sheldon)
</h2>
<p class="muted"><strong class="noWrap"><i class="fa fa-folder"></i>
Filed  10/01/2026 @ 3:04 PM</strong></p>
<table class="table table-striped"><thead><tr class="header"><th>#</th></tr></thead><tbody>
{
    senate_row(
        2,
        "09/04/2026",
        "Spouse",
        "JPM",
        "JP Morgan Chase &amp; Co. Common Stock",
        "Sale (Partial)",
        "$15,001 - $50,000",
    )
}
{
    senate_row(
        1,
        "09/03/2026",
        "Self",
        "BRK.B",
        "Berkshire Hathaway Inc.",
        "Purchase",
        "Over $50,000,000",
        "Rebalance",
    )
}
</tbody></table></body></html>""".encode()

SENATE_SEARCH = {
    "recordsTotal": 2,
    "recordsFiltered": 2,
    "data": [
        [
            "Sheldon",
            "Whitehouse",
            "Whitehouse, Sheldon (Senator)",
            '<a href="/search/view/ptr/abc-123/" target="_blank">Periodic Transaction Report</a>',
            "10/01/2026",
        ],
        [
            "Jane",
            "Doe",
            "Doe, Jane (Senator)",
            '<a href="/search/view/paper/def-456/" target="_blank">Periodic Transaction Report</a>',
            "09/15/2026",
        ],
    ],
}


def test_parse_house_ptr_reads_filer_and_options():
    report, rows = congress.parse_house_ptr("20034836", PELOSI)
    assert report == {
        "doc_id": "20034836",
        "chamber": "house",
        "name": "Nancy Pelosi",
        "state": "CA11",
        "filed": date(2026, 6, 23),
        "electronic": True,
    }
    assert [(r["ticker"], r["owner"], r["asset_type"], r["trans_type"]) for r in rows] == [
        ("INTC", "spouse", "OP", "purchase"),
        ("UBER", "spouse", "OP", "purchase"),
    ]
    intel = rows[0]
    assert intel["asset_name"] == "Intel Corporation - Common Stock"
    assert intel["trans_date"] == date(2026, 5, 29)
    assert (intel["amount_min"], intel["amount_max"]) == (1_000_001, 5_000_000)
    assert intel["comment"].startswith("Purchased 200 call options with a strike price of $50")


def test_parse_house_ptr_stitches_rows_cut_by_page_breaks():
    report, rows = congress.parse_house_ptr("20034660", WALBERG)
    assert report["name"] == "Tim Walberg"
    assert len(rows) == 16
    # Exxon's row starts on page one (amount "$15,001 -") and ends on page two ("$50,000").
    xom = next(r for r in rows if r["ticker"] == "XOM")
    assert xom["asset_name"] == "Exxon Mobil Corporation Common Stock"
    assert (xom["amount_min"], xom["amount_max"]) == (15_001, 50_000)
    assert xom["owner"] == "joint"
    # The row after it isn't swallowed into Exxon's asset name.
    fssl = [r for r in rows if r["ticker"] == "FSSL"]
    assert [r["trans_type"] for r in fssl] == ["purchase", "sale"]
    assert len({r["key"] for r in rows}) == 16


def test_parse_house_ptr_paper_filing_has_no_rows(monkeypatch):
    monkeypatch.setattr(congress, "_house_text", lambda data: "\n\n")
    report, rows = congress.parse_house_ptr("9116308", b"%PDF scanned")
    assert rows == [] and report["electronic"] is False


def test_parse_senate_ptr():
    report, rows = congress.parse_senate_ptr("abc-123", SENATE_PTR)
    assert report["name"] == "Sheldon Whitehouse"
    assert report["filed"] == date(2026, 10, 1)
    # Listed newest first on the page; stored in report order.
    brk, jpm = rows
    assert (brk["ticker"], brk["owner"], brk["trans_type"]) == ("BRK-B", "self", "purchase")
    assert (brk["amount_min"], brk["amount_max"]) == (50_000_001, None)
    assert brk["comment"] == "Rebalance"
    assert (jpm["ticker"], jpm["owner"], jpm["trans_type"]) == ("JPM", "spouse", "sale_partial")
    assert jpm["asset_name"] == "JP Morgan Chase & Co. Common Stock"
    assert jpm["comment"] is None


def test_house_index_keeps_ptrs_only():
    reports = congress.parse_house_index(
        house_index(
            ("20034836", "P", "Pelosi", "6/23/2026"),
            ("10071234", "A", "Pelosi", "5/15/2026"),  # annual report
            ("9116308", "P", "Smith", "7/01/2026"),  # paper
        )
    )
    assert [(r["doc_id"], r["electronic"], r["filed"]) for r in reports] == [
        ("20034836", True, date(2026, 6, 23)),
        ("9116308", False, date(2026, 7, 1)),
    ]


@respx.mock
def test_sync_house_then_rebuild(session, raw_store):
    respx.get(f"{HOUSE}/financial-pdfs/2026FD.zip").respond(
        200,
        content=house_index(
            ("20034836", "P", "Pelosi", "6/23/2026"),
            ("20034660", "P", "Walberg", "6/03/2026"),
            ("20099999", "P", "Gone", "6/04/2026"),
            ("9116308", "P", "Smith", "7/01/2026"),
        ),
    )
    respx.get(f"{HOUSE}/ptr-pdfs/2026/20034836.pdf").respond(200, content=PELOSI)
    respx.get(f"{HOUSE}/ptr-pdfs/2026/20034660.pdf").respond(200, content=WALBERG)
    respx.get(f"{HOUSE}/ptr-pdfs/2026/20099999.pdf").respond(404)
    house = HouseProvider(raw_store=raw_store)

    assert ingest.sync_house_index(session, house, 2026) == 4
    pending = congress.pending_reports(session, "house")
    assert {r.doc_id for r in pending} == {"20034836", "20034660", "20099999"}  # not paper
    for r in pending:
        ingest.sync_house_ptr(session, house, r.doc_id, 2026)
    assert congress.pending_reports(session, "house") == []
    assert session.get(CongressReport, "20034836").transactions == 2
    assert session.get(CongressReport, "20099999").transactions == 0  # withdrawn: not retried

    pelosi = congress.trades(session, member="pelosi")
    assert [t.ticker for t in pelosi] == ["INTC", "UBER"]
    assert pelosi[0].name == "Test Pelosi" and pelosi[0].state == "CA11"
    assert [t.name for t in congress.trades(session, ticker="xom")] == ["Test Walberg"]

    before = session.scalars(select(CongressTrade.key).order_by(CongressTrade.key)).all()
    loaded = rebuild(session, raw_store, "congress")
    assert loaded == {"house/fd_index": 1, "house/ptr": 2}
    assert session.scalars(select(CongressTrade.key).order_by(CongressTrade.key)).all() == before
    assert session.get(CongressReport, "20034836").name == "Test Pelosi"  # index wins


@respx.mock
def test_sync_senate(session, raw_store):
    home = respx.get(f"{SENATE}/search/home/").respond(
        200,
        text='<input type="hidden" name="csrfmiddlewaretoken" value="tok">',
        headers={"Set-Cookie": "csrftoken=cookie-tok; Path=/"},
    )
    agree = respx.post(f"{SENATE}/search/home/").respond(200)
    search = respx.post(f"{SENATE}/search/report/data/").respond(200, json=SENATE_SEARCH)
    respx.get(f"{SENATE}/search/view/ptr/abc-123/").respond(200, content=SENATE_PTR)
    senate = SenateProvider(raw_store=raw_store)

    assert ingest.sync_senate_index(session, senate, date(2026, 1, 1)) == 2
    assert home.called and agree.called
    request = search.calls.last.request
    assert request.headers["X-CSRFToken"] == "cookie-tok"
    assert b"submitted_start_date=01%2F01%2F2026" in request.content
    # The paper report is indexed but never fetched.
    assert [r.doc_id for r in congress.pending_reports(session, "senate")] == ["abc-123"]
    assert session.get(CongressReport, "def-456").electronic is False

    assert ingest.sync_senate_ptr(session, senate, "abc-123") == 2
    assert [t.ticker for t in congress.trades(session, member="whitehouse")] == ["JPM", "BRK-B"]
    # The recorded search request carries the form, not the session's token.
    params = json.loads(
        raw_store.engine.connect()
        .exec_driver_sql("select params from raw_responses where dataset = 'search'")
        .scalar()
    )
    assert "csrfmiddlewaretoken" not in params["body"]


@respx.mock
def test_senate_reaccepts_terms_when_session_lapses(raw_store):
    respx.get(f"{SENATE}/search/home/").respond(
        200, text='<input name="csrfmiddlewaretoken" value="tok">'
    )
    agree = respx.post(f"{SENATE}/search/home/").respond(200)
    respx.get(f"{SENATE}/search/view/ptr/abc-123/").mock(
        side_effect=[
            httpx.Response(302, headers={"Location": "/search/home/"}),
            httpx.Response(200, content=SENATE_PTR),
        ]
    )
    assert SenateProvider(raw_store=raw_store).fetch_ptr("abc-123") == SENATE_PTR
    assert agree.call_count == 2


def test_most_traded(session):
    congress.load_report(session, *congress.parse_house_ptr("20034836", PELOSI))
    congress.load_report(session, *congress.parse_senate_ptr("abc-123", SENATE_PTR))
    congress.load_report(
        session,
        {"doc_id": "x", "chamber": "house", "name": "Other Member"},
        [
            {
                "key": "k1",
                "doc_id": "x",
                "chamber": "house",
                "owner": "self",
                "ticker": "INTC",
                "asset_name": "Intel",
                "asset_type": "ST",
                "trans_type": "sale",
                "trans_date": date(2026, 6, 1),
                "notified": None,
                "amount_min": 1001.0,
                "amount_max": 15000.0,
                "comment": None,
            }
        ],
    )
    popular = congress.most_traded(session, days=180, as_of=date(2026, 10, 3))
    assert popular[0].ticker == "INTC"
    assert (popular[0].members, popular[0].purchases, popular[0].sales) == (2, 1, 1)
    assert session.scalar(select(func.count()).select_from(CongressTrade)) == 5
