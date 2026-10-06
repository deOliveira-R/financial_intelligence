from datetime import date

import respx
from sqlalchemy import func, select

from fin_intel import funds, ingest, thirteenf
from fin_intel.models import CusipMapping, FundHolding, Security
from fin_intel.providers import SecProvider
from fin_intel.rebuild import rebuild

AAPL, MSFT = "037833100", "594918104"


def nport(period, holdings):
    """holdings: (name, cusip, shares, value, weight[, isin])"""
    lines = "".join(
        f"""<invstOrSec><name>{h[0]}</name><lei>X</lei><title>{h[0]}</title><cusip>{h[1]}</cusip>
        <identifiers><isin value="{h[5] if len(h) > 5 else "US" + h[1] + "0"}"/></identifiers>
        <balance>{h[2]}</balance><units>NS</units><curCd>USD</curCd><valUSD>{h[3]}</valUSD>
        <pctVal>{h[4]}</pctVal><assetCat>EC</assetCat></invstOrSec>"""
        for h in holdings
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<edgarSubmission xmlns="http://www.sec.gov/edgar/nport"><formData><genInfo>
<seriesId>S000004310</seriesId><repPdEnd>2027-03-31</repPdEnd><repPdDate>{period}</repPdDate>
</genInfo><invstOrSecs>{lines}</invstOrSecs></formData></edgarSubmission>""".encode()


def test_placeholder_cusips_fall_back_to_isin():
    assert funds.valid_cusip(AAPL) and funds.valid_cusip("67066G104")
    assert not funds.valid_cusip("000000000") and not funds.valid_cusip("037833101")
    accenture = ("Accenture", "000000000", 1, 10, 3.0, "IE00B4BNMY34")
    linde = ("Linde", "000000000", 2, 20, 0.9, "IE000S9YS762")
    _, rows = funds.parse(nport("2020-12-31", [accenture, linde]))
    assert [(r["cusip"], r["identifier"]) for r in rows] == [
        (None, "IE00B4BNMY34"),
        (None, "IE000S9YS762"),
    ]


def test_parse_sums_lots_and_amendments_win(session):
    body = nport(
        "2026-06-30",
        [
            ("Apple Inc", AAPL, 100, 1000, 7.0),
            ("Apple Inc", AAPL, 10, 100, 0.7),
            ("Microsoft", MSFT, 50, 900, 6.3),
        ],
    )
    period, rows = funds.parse(body)
    assert period == date(2026, 6, 30) and len(rows) == 3
    funds.load(session, "IVV|S000004310|A-1|2026-08-25", body)
    amended = nport("2026-06-30", [("Apple Inc", AAPL, 120, 1200, 7.5)])
    funds.load(session, "IVV|S000004310|A-2|2026-09-01", amended)
    funds.load(session, "IVV|S000004310|A-1|2026-08-25", body)  # older, replayed late: ignored
    rows = funds.members(session, "IVV")
    assert [(r["cusip"], r["shares"]) for r in rows] == [(AAPL, 120)]
    # The amendment replaced the original: the period counts from the amendment's filing.
    assert funds.members(session, "IVV", date(2026, 8, 30)) == []


@respx.mock
def test_sync_fund_maps_series_fetches_new_reports_and_rebuilds(session, raw_store):
    respx.get("https://www.sec.gov/files/company_tickers_mf.json").respond(
        json={
            "fields": ["cik", "seriesId", "classId", "symbol"],
            "data": [[1100663, "S000004310", "C000012040", "IVV"], [1, "S1", "C1", "NOTTRACKED"]],
        }
    )
    atom = """<feed><entry><content><accession-number>0001-26-000002</accession-number>
    <filing-date>2026-08-25</filing-date><filing-type>NPORT-P</filing-type></content></entry>
    <entry><content><accession-number>0001-26-000001</accession-number>
    <filing-date>2026-05-28</filing-date><filing-type>NPORT-P</filing-type></content></entry></feed>"""
    respx.get(url__regex=r".*browse-edgar.*type=NPORT-P&.*").respond(text=atom)
    respx.get(url__regex=r".*browse-edgar.*type=NPORT-P%2FA.*").respond(text="<feed></feed>")
    respx.get(
        "https://www.sec.gov/Archives/edgar/data/1100663/000126000001/primary_doc.xml"
    ).respond(content=nport("2026-03-31", [("Apple Inc", AAPL, 100, 1000, 7.0)]))
    second = respx.get(
        "https://www.sec.gov/Archives/edgar/data/1100663/000126000002/primary_doc.xml"
    ).respond(content=nport("2026-06-30", [("Microsoft", MSFT, 50, 900, 6.3)]))
    sec = SecProvider(raw_store=raw_store)
    series = ingest.fund_series(sec)
    assert series == {"IVV": {"cik": 1100663, "series_id": "S000004310"}}
    assert ingest.sync_fund(session, sec, "IVV", series["IVV"]) == 2
    ingest.sync_fund(session, sec, "IVV", series["IVV"])
    assert second.call_count == 1  # stored reports aren't fetched again

    security = Security(ticker="MSFT", origin="sec")
    session.add(security)
    session.flush()
    session.add(CusipMapping(cusip=MSFT, security_id=security.id))
    session.commit()
    assert funds.members(session, "IVV")[0]["security_id"] == security.id
    assert funds.members(session, "IVV", date(2026, 6, 1))[0]["cusip"] == AAPL  # point in time
    assert AAPL in thirteenf.unmapped_cusips(session)  # fund CUSIPs join the OpenFIGI queue

    def count():
        return session.scalar(select(func.count()).select_from(FundHolding))

    before = count()
    with respx.mock:
        rebuild(session, raw_store, "funds")
    assert count() == before
