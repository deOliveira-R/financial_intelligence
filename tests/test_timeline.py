from datetime import date

import pytest
from fastapi.testclient import TestClient

from fin_intel import timeline
from fin_intel.api import app
from fin_intel.db import get_session, session_factory
from fin_intel.models import (
    Bill,
    BillSubject,
    CongressReport,
    CongressTrade,
    CorporateEvent,
    CusipMapping,
    EconomicRelease,
    EconomicReleaseDate,
    EconomicSeries,
    FederalObligation,
    Filing,
    GraphEdge,
    GraphNode,
    InsiderTransaction,
    InstitutionalFiler,
    InstitutionalPosition,
    Issuer,
    LobbyingIssue,
    LobbyingReport,
    PresidentialDocument,
    PresidentialText,
    Security,
    ShortInterest,
)


def insider(key, filed, code="P", shares=1000, price=50.0, cik=1):
    return InsiderTransaction(
        key=key,
        accession=f"acc-{key}",
        filing_date=filed,
        issuer_cik=cik,
        owner_name="DOE JANE",
        owner_title="CEO",
        trans_date=date(2026, 6, 1),
        trans_code=code,
        shares=shares,
        price=price,
    )


@pytest.fixture
def data(session):
    session.add_all([Issuer(cik=1, name="Aero"), Issuer(cik=2, name="Other")])
    session.flush()
    session.add_all([Security(ticker="AERO", cik=1), Security(ticker="OTHR", cik=2)])
    session.add_all(
        [
            # Public on the filing date, not the trade date.
            insider("buy", date(2026, 6, 3)),
            insider("late", date(2026, 7, 15)),
            insider("grant", date(2026, 6, 3), code="A"),
            insider("tiny", date(2026, 6, 3), shares=10),
            insider("other", date(2026, 6, 3), cik=2),
            CongressReport(
                doc_id="d1", chamber="house", name="Rep. Smith", filed=date(2026, 6, 20)
            ),
            CorporateEvent(accession="a1", item="2.02", cik=1, form="8-K", filed=date(2026, 6, 10)),
            Filing(
                accession="f1",
                cik=1,
                form="10-Q",
                filed=date(2026, 6, 5),
                fiscal_year=2026,
                fiscal_period="Q2",
            ),
            Bill(
                id="119-hr-1",
                congress=119,
                type="hr",
                number=1,
                introduced=date(2026, 6, 2),
                title="Munitions Production Act",
                policy_area="Health",
            ),
            Bill(
                id="119-hr-2",
                congress=119,
                type="hr",
                number=2,
                introduced=date(2026, 6, 2),
                title="Spam Calls Act",
                policy_area="Armed Forces and National Security",
            ),
            Bill(
                id="119-hr-3",
                congress=119,
                type="hr",
                number=3,
                introduced=date(2026, 6, 4),
                title="Readiness Act",
                policy_area="Armed Forces and National Security",
            ),
            PresidentialDocument(
                document_number="p1",
                kind="executive_order",
                number="14000",
                title="Invoking the Defense Production Act",
                signed=date(2026, 6, 6),
            ),
            PresidentialDocument(
                document_number="p2",
                kind="proclamation",
                number="11000",
                title="National Fishing Day",
                signed=date(2026, 6, 7),
            ),
            LobbyingReport(
                filing_uuid="l1",
                year=2026,
                quarter=1,
                filing_type="Q1",
                client="AERO CORP",
                registrant="AERO CORP",
                amount=500000.0,
                posted=date(2026, 6, 15),
            ),
            LobbyingReport(
                filing_uuid="l2",
                year=2026,
                quarter=1,
                filing_type="Q1",
                client="TRADE GROUP",
                registrant="K STREET",
                amount=900000.0,
                posted=date(2026, 6, 15),
            ),
            FederalObligation(month=date(2026, 3, 1), naics="336411", amount=2e9),
            FederalObligation(month=date(2025, 3, 1), naics="336411", amount=1e9),
            GraphNode(key="company:1", kind="company", label="Aero (AERO)"),
            GraphNode(key="theme:defense", kind="theme", label="Defense"),
            GraphEdge(src="company:1", dst="theme:defense", kind="part_of", source="themes"),
        ]
    )
    session.flush()
    session.add_all(
        [
            CongressTrade(
                key="t1",
                doc_id="d1",
                chamber="house",
                ticker="AERO",
                trans_type="purchase",
                trans_date=date(2026, 6, 1),
                amount_min=1001.0,
                amount_max=15000.0,
            ),
            BillSubject(
                bill_id="119-hr-3", subject="Military procurement, research, weapons development"
            ),
            PresidentialText(document_number="p1", text="Defense Production Act section 303..."),
            PresidentialText(document_number="p2", text="Fishing. Our armed forces once said..."),
            LobbyingIssue(filing_uuid="l1", code="DEF", description="Munitions procurement"),
            LobbyingIssue(filing_uuid="l2", code="DEF", description="Tariffs on lumber"),
        ]
    )
    session.commit()
    return session


def refs(events):
    return {e.ref.split(":", 1)[1] for e in events}


def test_company_timeline_is_point_in_time(data):
    events = timeline.query(data, date(2026, 6, 1), date(2026, 6, 30), ciks={1})
    kinds = {e.kind for e in events}
    assert kinds == {"insider", "congress", "filing_event", "report"}
    insiders = [e for e in events if e.kind == "insider"]
    # Grants and trades under MIN_INSIDER_VALUE are left out; July's filing isn't public yet.
    assert refs(insiders) == {"buy"}
    assert insiders[0].amount == 50000 and insiders[0].ticker == "AERO"
    (trade,) = [e for e in events if e.kind == "congress"]
    assert trade.date == date(2026, 6, 20) and trade.cik == 1  # filed, not traded
    assert "Rep. Smith purchase AERO" in trade.title
    assert events == sorted(events, key=lambda e: (e.date, e.kind), reverse=True)


def test_theme_timeline(data):
    events = timeline.query(data, date(2026, 6, 1), date(2026, 6, 30), theme="defense")
    by_kind: dict[str, set[str]] = {}
    for e in events:
        by_kind.setdefault(e.kind, set()).add(e.ref.split(":", 1)[1])
    # Company events come from the theme's companies (graph part_of edges).
    assert by_kind["insider"] == {"buy"}
    # Bills: keywords in the title, or policy area and subject together (area alone isn't).
    assert by_kind["bill"] == {"119-hr-1", "119-hr-3"}
    # Presidential: keywords in the title; a passing mention in the text ranks too low.
    assert by_kind["presidential"] == {"p1"}
    # Lobbying: the theme's issue codes and keywords in the issue description.
    assert by_kind["lobbying"] == {"l1"}
    (contracts,) = [e for e in events if e.kind == "contracts"]
    assert contracts.date == date(2026, 6, 29) and "+100% on a year earlier" in contracts.title


def test_kinds_and_unscoped(data):
    only = timeline.query(data, date(2026, 6, 1), date(2026, 6, 30), kinds=["bill"])
    assert {e.kind for e in only} == {"bill"}
    unscoped = timeline.query(data, date(2026, 6, 1), date(2026, 6, 30))
    assert "other" in refs(e for e in unscoped if e.kind == "insider")
    assert not any(e.kind in ("lobbying", "contracts") for e in unscoped)
    with pytest.raises(ValueError):
        timeline.query(data, date(2026, 6, 1), date(2026, 6, 30), kinds=["gossip"])


def test_api(data, engine):
    def override():
        with session_factory(engine)() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        client = TestClient(app)
        params = {"ticker": "AERO", "start": "2026-06-01", "end": "2026-06-30"}
        events = client.get("/timeline", params=params).json()
        assert {e["kind"] for e in events} == {"insider", "congress", "filing_event", "report"}
        assert client.get("/timeline", params={"ticker": "NOPE"}).status_code == 404
        assert client.get("/timeline", params={"kind": "gossip"}).status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_short_interest_and_13f(data):
    session = data
    sid = session.query(Security).filter_by(ticker="AERO").one().id
    session.add_all([InstitutionalFiler(cik=c, name=str(c)) for c in (10, 11, 12)])
    session.add_all(
        [
            CusipMapping(cusip="AERO00001", security_id=sid),
            CusipMapping(cusip="AERO00002", security_id=sid),  # a second share class
        ]
    )
    session.flush()

    def pos(filer, period, filed, cusip="AERO00001", shares=100.0):
        return InstitutionalPosition(
            filer_cik=filer,
            period=period,
            cusip=cusip,
            put_call="",
            shares=shares,
            accession=f"{filer}{period}{cusip}",
            filed=filed,
        )

    session.add_all(
        [
            ShortInterest(security_id=sid, settlement_date=date(2026, 5, 15), short_position=100.0),
            ShortInterest(security_id=sid, settlement_date=date(2026, 5, 29), short_position=110.0),
            ShortInterest(
                security_id=sid,
                settlement_date=date(2026, 6, 15),
                short_position=165.0,
                days_to_cover=3.0,
            ),
            pos(10, date(2025, 12, 31), date(2026, 2, 1)),
            pos(10, date(2026, 3, 31), date(2026, 5, 1)),
            pos(10, date(2026, 3, 31), date(2026, 5, 1), cusip="AERO00002"),  # one holder
            pos(11, date(2026, 3, 31), date(2026, 5, 2), shares=50.0),
            pos(12, date(2026, 3, 31), date(2026, 9, 1)),  # late: after the deadline
        ]
    )
    session.commit()
    events = timeline.query(
        session, date(2026, 5, 1), date(2026, 6, 30), ciks={1}, kinds=["short_interest", "13f"]
    )
    (si,) = [e for e in events if e.kind == "short_interest"]  # +10% isn't an event
    assert si.date == date(2026, 6, 25) and "+50% to 165 shares, 3.0 days to cover" in si.title
    (holders,) = [e for e in events if e.kind == "13f"]
    assert holders.date == date(2026, 5, 15)
    assert "2 holders (+1), shares held +150.0%" in holders.title


def test_releases(session):
    session.add_all(
        [
            EconomicRelease(id=10, name="Consumer Price Index"),
            EconomicRelease(id=13, name="G.17 Industrial Production and Capacity Utilization"),
            EconomicRelease(id=18, name="H.15 Selected Interest Rates"),
        ]
    )
    session.flush()
    session.add_all(
        [
            EconomicSeries(id="CPIAUCSL", source="fred", frequency="M", release_id=10),
            EconomicSeries(id="INDPRO", source="fred", frequency="M", release_id=13),
            EconomicSeries(id="TCU", source="fred", frequency="M", release_id=13),
            EconomicSeries(id="DFF", source="fred", frequency="D", release_id=18),
            EconomicReleaseDate(release_id=10, date=date(2026, 6, 10)),
            EconomicReleaseDate(release_id=13, date=date(2026, 6, 16)),
            EconomicReleaseDate(release_id=13, date=date(2026, 7, 15)),
            EconomicReleaseDate(release_id=18, date=date(2026, 6, 15)),
        ]
    )
    session.commit()
    june = date(2026, 6, 1), date(2026, 6, 30)
    # Daily releases (rates published every day) aren't events.
    unscoped = timeline.query(session, *june, kinds=["release"])
    assert [(e.date, e.ref) for e in unscoped] == [
        (date(2026, 6, 16), "economic_releases:13"),
        (date(2026, 6, 10), "economic_releases:10"),
    ]
    (indpro,) = timeline.query(session, *june, theme="industrials", kinds=["release"])
    assert indpro.title.endswith("(INDPRO, TCU)")
    assert timeline.query(session, *june, theme="financials", kinds=["release"]) == []
    assert timeline.query(session, *june, ciks={1}, kinds=["release"]) == []
