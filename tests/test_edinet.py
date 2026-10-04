from datetime import date

from sqlalchemy import select

from fin_intel import ingest, world
from fin_intel.models import Issuer, StatementItem

JPPFS = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor"
JPCRP = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2025-11-01/jpcrp_cor"
JPDEI = "http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor"


def instance() -> bytes:
    def ctx(cid, start=None, end=None, instant=None, nonconsolidated=False):
        axis = "jppfs_cor:ConsolidatedOrNonConsolidatedAxis"
        scenario = (
            f'<xbrli:scenario><xbrldi:explicitMember dimension="{axis}">'
            "jppfs_cor:NonConsolidatedMember</xbrldi:explicitMember></xbrli:scenario>"
            if nonconsolidated
            else ""
        )
        period = (
            f"<xbrli:instant>{instant}</xbrli:instant>"
            if instant
            else f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
        )
        return (
            f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier '
            f'scheme="http://disclosure.edinet-fsa.go.jp">E00776-000</xbrli:identifier>'
            f"</xbrli:entity><xbrli:period>{period}</xbrli:period>{scenario}</xbrli:context>"
        )

    def fact(prefix, concept, context, unit, value):
        unit_attr = f' unitRef="{unit}"' if unit else ""
        return f'<{prefix}:{concept} contextRef="{context}"{unit_attr}>{value}</{prefix}:{concept}>'

    shares = "NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc"
    year, year_nc = "CurrentYearDuration", "CurrentYearDuration_NonConsolidatedMember"
    facts = "".join(
        [
            fact(
                "jpdei_cor", "FilerNameInEnglishDEI", "Filing", None, "Shin-Etsu Chemical Co., Ltd."
            ),
            fact("jpdei_cor", "SecurityCodeDEI", "Filing", None, "40630"),
            fact("jpdei_cor", "CurrentFiscalYearEndDateDEI", "Filing", None, "2026-03-31"),
            fact("jpdei_cor", "TypeOfCurrentPeriodDEI", "Filing", None, "FY"),
            fact("jppfs_cor", "NetSales", year, "JPY", 2809000000000),
            fact("jppfs_cor", "NetSales", year_nc, "JPY", 1),
            fact("jppfs_cor", "OperatingIncome", year, "JPY", 742000000000),
            fact("jppfs_cor", "ShortTermLoansPayable", "CurrentYearInstant", "JPY", 100),
            fact("jppfs_cor", "LongTermLoansPayable", "CurrentYearInstant", "JPY", 200),
            fact("jppfs_cor", "BondsPayable", "CurrentYearInstant", "JPY", 50),
            fact("jpcrp_cor", shares, "CurrentYearInstant", "shares", 2000),
            fact(
                "jpcrp_cor",
                "TotalNumberOfSharesHeldTreasurySharesEtc",
                "CurrentYearInstant",
                "shares",
                15,
            ),
        ]
    )
    contexts = "".join(
        [
            ctx(year, "2025-04-01", "2026-03-31"),
            ctx(year_nc, "2025-04-01", "2026-03-31", nonconsolidated=True),
            ctx("CurrentYearInstant", instant="2026-03-31"),
            ctx("Filing", instant="2026-06-19"),
        ]
    )
    return f"""<?xml version="1.0"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:jppfs_cor="{JPPFS}"
  xmlns:jpcrp_cor="{JPCRP}" xmlns:jpdei_cor="{JPDEI}" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
  xmlns:iso4217="http://www.xbrl.org/2003/iso4217">
  {contexts}
  <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
  <xbrli:unit id="shares"><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unit>
  {facts}
</xbrli:xbrl>""".encode()


def test_edinet_report_loads_consolidated_statements(session):
    rows = ingest.load_edinet_instance(session, "S100YE9I|E00776|120|2026-06-19", instance())
    assert rows > 0
    cik = world.issuer_id("edinet", "E00776")
    issuer = session.get(Issuer, cik)
    assert (issuer.name, issuer.home_ticker, issuer.fiscal_month, issuer.country) == (
        "Shin-Etsu Chemical Co., Ltd.",
        "4063",
        3,
        "JP",
    )
    items = dict(
        session.execute(
            select(StatementItem.line_item, StatementItem.value).where(
                StatementItem.cik == cik, StatementItem.period_end == date(2026, 3, 31)
            )
        ).all()
    )
    assert items["revenue"] == 2809000000000  # consolidated, not the parent-only figure
    assert items["operating_income"] == 742000000000
    assert items["long_term_debt"] == 350  # loans + bonds
    assert items["shares_outstanding"] == 1985  # issued minus treasury
    labels = set(
        session.scalars(select(StatementItem.fiscal_period).where(StatementItem.cik == cik))
    )
    assert "FY" in labels
