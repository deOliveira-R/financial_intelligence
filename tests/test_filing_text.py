from datetime import date

import respx
from sqlalchemy import func, select

from fin_intel import filing_text, ingest
from fin_intel.models import FilingText
from fin_intel.providers import SecProvider
from fin_intel.rebuild import rebuild

RISKS = "Export controls on advanced chips to China could reduce our revenue. " * 20
MDNA = "Revenue grew because data center demand for accelerated computing rose. " * 20

TEN_K = f"""<html><body><ix:header><ix:hidden>dei stuff</ix:hidden></ix:header>
<table><tr><td>Item 1A.</td><td>Risk Factors</td><td>12</td></tr>
<tr><td>Item 1B.</td><td>Unresolved Staff Comments</td><td>30</td></tr>
<tr><td>Item 7.</td><td>Management's Discussion</td><td>40</td></tr>
<tr><td>Item 7A.</td><td>Quantitative and Qualitative Disclosures</td><td>55</td></tr>
<tr><td>Item 8.</td><td>Financial Statements</td><td>57</td></tr></table>
<p>PART I</p><p><b>Item 1A. Risk Factors</b></p><p>{RISKS}</p>
<p>Item 1B. Unresolved Staff Comments</p><p>None.</p>
<div>Item 7. Management&#8217;s Discussion and Analysis of Financial Condition</div>
<p>{MDNA}</p><p>Item 7A. Quantitative and Qualitative Disclosures</p><p>Rates.</p>
</body></html>""".encode()

EXXON_STYLE = f"""<p>Item 7. Management's Discussion and Analysis: see the Financial Section.</p>
<p>Item 8. Financial Statements: see the Financial Section.</p>
<p>MANAGEMENT’S DISCUSSION AND ANALYSIS OF FINANCIAL CONDITION AND RESULTS OF OPERATIONS</p>
<p>{MDNA}</p><p>MANAGEMENT’S REPORT ON INTERNAL CONTROL OVER FINANCIAL REPORTING</p>""".encode()

ROW = '<tr><td>{}</td><td>{}</td><td><a href="{}">doc</a></td><td>{}</td></tr>'
INDEX_PAGE = "".join(
    ROW.format(*r)
    for r in [
        (1, "8-K", "/ix?doc=/Archives/edgar/data/1/0001-26-000003/a.htm", "8-K"),
        (2, "PRESS RELEASE", "/Archives/edgar/data/1/000126000003/pr.htm", "EX-99.1"),
        (3, "LOGO", "/Archives/edgar/data/1/000126000003/logo.jpg", "GRAPHIC"),
    ]
).encode()


def test_sections_skip_the_table_of_contents():
    text = filing_text.to_text(TEN_K)
    assert "dei stuff" not in text
    risks = filing_text.section(text, "10-K", "risk_factors")
    assert risks.startswith("Item 1A. Risk Factors") and "Unresolved" not in risks
    assert "accelerated computing" in filing_text.section(text, "10-K", "mdna")
    exxon = filing_text.section(filing_text.to_text(EXXON_STYLE), "10-K", "mdna")
    assert exxon.startswith("MANAGEMENT’S DISCUSSION") and "INTERNAL CONTROL" not in exxon
    assert [(e.document, e.kind) for e in filing_text.exhibits(INDEX_PAGE)] == [
        ("pr.htm", "EX-99.1")
    ]


@respx.mock
def test_sync_search_and_rebuild(session, raw_store):
    submissions = {
        "cik": "1",
        "filings": {
            "recent": {
                "accessionNumber": ["0001-26-000003", "0001-26-000002", "0001-22-000001"],
                "form": ["8-K", "10-K", "10-K"],
                "filingDate": ["2026-02-25", "2026-02-25", "2022-02-25"],
                "reportDate": ["2026-02-25", "2026-01-25", "2022-01-30"],
                "primaryDocument": ["a.htm", "k.htm", "old.htm"],
                "items": ["2.02,9.01", "", ""],
            },
            "files": [],
        },
    }
    respx.get("https://data.sec.gov/submissions/CIK0000000001.json").respond(json=submissions)
    archive = "https://www.sec.gov/Archives/edgar/data/1"
    report = respx.get(f"{archive}/000126000002/k.htm").respond(content=TEN_K)
    respx.get(f"{archive}/000126000003/0001-26-000003-index.htm").respond(content=INDEX_PAGE)
    respx.get(f"{archive}/000126000003/pr.htm").respond(
        content=b"<p>Record quarterly revenue of $50 billion, up 60% from a year ago.</p>"
    )
    sec = SecProvider(raw_store=raw_store)
    assert ingest.sync_filing_text(session, sec, 1, date(2023, 1, 1)) == 3
    ingest.sync_filing_text(session, sec, 1, date(2023, 1, 1))
    assert report.call_count == 1  # fetched documents aren't fetched again

    hits = filing_text.search(session, '"export controls" china')
    assert [(h.form, h.section) for h in hits] == [("10-K", "risk_factors")]
    assert "[Export controls] on advanced chips to [China]" in hits[0].snippet
    assert filing_text.search(session, "quarterly revenue", ciks=[2]) == []
    assert filing_text.search(session, "quarterly revenue", ciks=[1])[0].section == "EX-99.1"

    def count():
        return session.scalar(select(func.count()).select_from(FilingText))

    before = count()
    with respx.mock:
        rebuild(session, raw_store, "texts")
    assert count() == before
    assert len(filing_text.search(session, "accelerated computing")) == 1  # index rebuilt too
