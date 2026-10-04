from datetime import date

import respx
from sqlalchemy import select

from fin_intel import ingest, xbrl
from fin_intel.models import Filing, Issuer, StatementItem
from fin_intel.providers import SecProvider
from fin_intel.rebuild import rebuild

IFRS = "https://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full"


def instance() -> bytes:
    def ctx(cid, start=None, end=None, instant=None, dim=False):
        seg = (
            '<segment><xbrldi:explicitMember dimension="ifrs-full:GeographicalAreasAxis">'
            "country:US</xbrldi:explicitMember></segment>"
            if dim
            else ""
        )
        period = (
            f"<instant>{instant}</instant>"
            if instant
            else f"<startDate>{start}</startDate><endDate>{end}</endDate>"
        )
        return (
            f'<context id="{cid}"><entity><identifier scheme="http://www.sec.gov/CIK">'
            f"0001046179</identifier>{seg}</entity><period>{period}</period></context>"
        )

    def fact(concept, context, unit, value):
        return (
            f'<ifrs-full:{concept} contextRef="{context}" unitRef="{unit}" decimals="-5">'
            f"{value}</ifrs-full:{concept}>"
        )

    revenue = "RevenueFromContractsWithCustomers"
    facts = "".join(
        [
            fact(revenue, "y24", "twd", 2894300000000),
            fact(revenue, "y25", "twd", 3809000000000),
            fact(revenue, "y25", "usd", 121400000000),  # convenience translation
            fact(revenue, "us25", "twd", 2700000000000),  # a geographic breakdown
            fact("ProfitLoss", "y25", "twd", 1695124900000),
            fact("BasicEarningsLossPerShare", "y25", "eps", 65.47),
            fact("Assets", "i25", "twd", 7932842500000),
            '<ifrs-full:Assets contextRef="i25" unitRef="twd" xsi:nil="true"/>',
        ]
    )
    return f"""<?xml version="1.0"?>
<xbrl xmlns="http://www.xbrl.org/2003/instance" xmlns:ifrs-full="{IFRS}"
      xmlns:dei="http://xbrl.sec.gov/dei/2025" xmlns:tsm="http://www.tsmc.com/20251231"
      xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
      xmlns:xbrli="http://www.xbrl.org/2003/instance"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  {ctx("y24", "2024-01-01", "2024-12-31")}{ctx("y25", "2025-01-01", "2025-12-31")}
  {ctx("i25", instant="2025-12-31")}{ctx("us25", "2025-01-01", "2025-12-31", dim=True)}
  <unit id="twd"><measure>iso4217:TWD</measure></unit>
  <unit id="usd"><measure>iso4217:USD</measure></unit>
  <unit id="eps"><divide><unitNumerator><measure>iso4217:TWD</measure></unitNumerator>
    <unitDenominator><measure>xbrli:shares</measure></unitDenominator></divide></unit>
  <dei:EntityRegistrantName contextRef="y25">Taiwan Semiconductor</dei:EntityRegistrantName>
  <dei:DocumentFiscalYearFocus contextRef="y25">2025</dei:DocumentFiscalYearFocus>
  <dei:DocumentFiscalPeriodFocus contextRef="y25">FY</dei:DocumentFiscalPeriodFocus>
  {facts}
  <tsm:WaferShipments contextRef="y25" unitRef="twd">1</tsm:WaferShipments>
</xbrl>""".encode()


def test_instance_file_prefers_the_inline_extract():
    names = [
        "tsm-20251231.htm",
        "tsm-20251231_cal.xml",
        "tsm-20251231_htm.xml",
        "FilingSummary.xml",
    ]
    assert xbrl.instance_file(names) == "tsm-20251231_htm.xml"
    assert xbrl.instance_file(["abc-20101231.xml", "abc-20101231_pre.xml"]) == "abc-20101231.xml"
    assert xbrl.instance_file(["x.htm"]) is None


def test_parse_keeps_standard_undimensioned_facts():
    p = xbrl.parse_instance(instance(), "0001628280-26-025362", "20-F", date(2026, 4, 16))
    assert p["entityName"] == "Taiwan Semiconductor"
    assert set(p["facts"]) == {"ifrs-full"}  # dei text facts aren't values; extensions dropped
    revenue = p["facts"]["ifrs-full"]["RevenueFromContractsWithCustomers"]["units"]
    assert [(f["start"], f["val"]) for f in revenue["TWD"]] == [
        ("2024-01-01", 2894300000000.0),
        ("2025-01-01", 3809000000000.0),  # the US-only breakdown isn't the total
    ]
    assert revenue["TWD"][1]["fy"] == 2025 and revenue["TWD"][1]["filed"] == "2026-04-16"
    assert (
        p["facts"]["ifrs-full"]["BasicEarningsLossPerShare"]["units"]["TWD/shares"][0]["val"]
        == 65.47
    )
    (assets,) = p["facts"]["ifrs-full"]["Assets"]["units"]["TWD"]
    assert "start" not in assets and assets["end"] == "2025-12-31"


@respx.mock
def test_fill_gaps_loads_the_filing_and_keeps_the_reporting_currency(session, raw_store):
    session.add(Issuer(cik=1046179, name="TSMC"))
    session.commit()
    accession = "0001628280-26-025362"
    respx.get("https://data.sec.gov/submissions/CIK0001046179.json").respond(
        json={
            "sic": "3674",
            "category": "Large accelerated filer",
            "filings": {
                "recent": {
                    "accessionNumber": [accession, "0001628280-26-000001"],
                    "form": ["20-F", "6-K"],
                    "filingDate": ["2026-04-16", "2026-04-10"],
                    "isXBRL": [1, 0],
                }
            },
        }
    )
    folder = "https://www.sec.gov/Archives/edgar/data/1046179/000162828026025362"
    respx.get(f"{folder}/index.json").respond(
        json={
            "directory": {"item": [{"name": "tsm-20251231.htm"}, {"name": "tsm-20251231_htm.xml"}]}
        }
    )
    respx.get(f"{folder}/tsm-20251231_htm.xml").respond(content=instance())
    sec = SecProvider(raw_store=raw_store)

    assert ingest.sync_xbrl_gaps(session, sec, 1046179) > 0
    filing = session.scalar(select(Filing).where(Filing.accession == accession))
    assert (filing.form, filing.filed) == ("20-F", date(2026, 4, 16))
    assert session.get(Issuer, 1046179).name == "TSMC"  # not overwritten

    def revenue():
        return session.execute(
            select(StatementItem.period_end, StatementItem.unit, StatementItem.value)
            .where(StatementItem.cik == 1046179, StatementItem.line_item == "revenue")
            .order_by(StatementItem.period_end)
        ).all()

    # The USD convenience translation doesn't displace the TWD figure.
    expected = [
        (date(2024, 12, 31), "TWD", 2894300000000.0),
        (date(2025, 12, 31), "TWD", 3809000000000.0),
    ]
    assert revenue() == expected
    assert ingest.sync_xbrl_gaps(session, sec, 1046179) == 0  # loaded now: not refetched

    rebuild(session, raw_store, "fundamentals")
    assert revenue() == expected
