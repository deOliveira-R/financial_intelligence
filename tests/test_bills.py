import io
import zipfile
from datetime import date

import respx
from sqlalchemy import func, select
from test_timeseries import add_bars

from fin_intel import bills, ingest, timeseries
from fin_intel.models import Bill, BillSubject
from fin_intel.providers import GovinfoProvider
from fin_intel.rebuild import rebuild


def status(number, area, introduced, law=None, subjects=()):
    actions = f"<item><actionDate>{introduced}</actionDate><text>Introduced in House</text></item>"
    if law:
        actions += (
            f"<item><actionDate>{law}</actionDate><text>Became Public Law No: 118-31.</text></item>"
        )
    laws = "<laws><item><type>Public Law</type><number>118-31</number></item></laws>" if law else ""
    subject_items = "".join(f"<item><name>{s}</name></item>" for s in subjects)
    return f"""<?xml version="1.0"?><billStatus><bill><number>{number}</number><type>HR</type>
    <introducedDate>{introduced}</introducedDate><congress>118</congress>
    <committees><item><systemCode>hsas00</systemCode></item></committees>
    <relatedBills><item><title>Some other bill</title></item></relatedBills>
    <actions>{actions}</actions>
    <sponsors><item><bioguideId>R000575</bioguideId><party>R</party></item></sponsors>
    <cosponsors><item><bioguideId>A1</bioguideId></item><item><bioguideId>A2</bioguideId></item></cosponsors>
    {laws}
    <policyArea><name>{area}</name></policyArea>
    <subjects><legislativeSubjects>{subject_items}</legislativeSubjects></subjects>
    <title>An act about {area}</title>
    <latestAction><actionDate>{law or introduced}</actionDate><text>Latest</text></latestAction>
    </bill></billStatus>""".encode()


def bulk(docs):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, doc in enumerate(docs):
            z.writestr(f"BILLSTATUS-118hr{i}.xml", doc)
    return buf.getvalue()


def test_parse_reads_the_bill_not_related_bills():
    row, subjects = bills.parse(
        status(
            2670,
            "Armed Forces and National Security",
            "2023-04-18",
            "2023-12-22",
            ["Military procurement"],
        )
    )
    assert (
        row["id"] == "118-hr-2670"
        and row["title"] == "An act about Armed Forces and National Security"
    )
    assert (row["sponsor"], row["cosponsors"], row["committees"]) == ("R000575", 2, "hsas00")
    assert (row["law"], row["enacted"]) == ("118-31", date(2023, 12, 22))
    assert subjects == ["Military procurement"]
    assert (
        bills.congress_for(date(2026, 10, 6)) == 119 and bills.congress_for(date(2011, 1, 5)) == 112
    )


@respx.mock
def test_sync_counts_and_rebuild(session, raw_store):
    defense = "Armed Forces and National Security"
    hr = bulk(
        [
            status(1, defense, "2024-03-05", "2024-06-10", ["Military procurement"]),
            status(2, defense, "2024-03-20"),
            status(3, "Energy", "2024-04-02"),
        ]
    )
    respx.get(url__regex=r".*/BILLSTATUS/118/hr/.*").respond(content=hr)
    respx.get(url__regex=r".*/BILLSTATUS/118/(s|hjres|sjres)/.*").respond(content=bulk([]))
    assert ingest.sync_bills(session, GovinfoProvider(raw_store=raw_store), 118) == 3
    assert bills.monthly(session, "defense") == [(date(2024, 3, 1), 2)]
    assert bills.monthly(session, "defense", enacted=True) == [(date(2024, 6, 1), 1)]

    add_bars(session, "SPY", date(2024, 3, 1), [500.0] * 150)
    days, series = timeseries.build(session, ["bills:defense", "bills:defense:law"])
    values = dict(zip(days, series["bills:defense"], strict=True))
    assert values[date(2024, 3, 31)] is None and values[date(2024, 4, 1)] == 2  # month complete
    assert series["bills:defense:law"][-1] == 1

    def count(model):
        return session.scalar(select(func.count()).select_from(model))

    before = (count(Bill), count(BillSubject))
    with respx.mock:
        rebuild(session, raw_store, "bills")
    assert (count(Bill), count(BillSubject)) == before
