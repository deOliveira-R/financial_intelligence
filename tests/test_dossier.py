from datetime import date

from fastapi.testclient import TestClient

from fin_intel import dossier
from fin_intel.api import app
from fin_intel.db import get_session, session_factory
from fin_intel.models import (
    CompanyMetrics,
    CusipMapping,
    FilingText,
    FundHolding,
    GraphEdge,
    GraphNode,
    InstitutionalFiler,
    InstitutionalPosition,
    Issuer,
    Security,
    ShortInterest,
)

AS_OF = date(2026, 6, 30)


def position(filer, period, filed, shares):
    return InstitutionalPosition(
        filer_cik=filer,
        period=period,
        cusip="AERO00001",
        put_call="",
        shares=shares,
        value=shares * 10.0,
        accession=f"{filer}-{period}",
        filed=filed,
    )


def setup(session):
    session.add(Issuer(cik=1, name="Aero", sic=3721))
    session.add_all(
        [InstitutionalFiler(cik=10, name="Big Fund"), InstitutionalFiler(cik=11, name="Small Fund")]
    )
    session.flush()
    security = Security(ticker="AERO", cik=1)
    session.add(security)
    session.flush()
    sid = security.id
    session.add_all(
        [
            CompanyMetrics(
                security_id=sid,
                as_of=date(2026, 6, 1),
                cik=1,
                price=10.0,
                period_end=date(2026, 3, 31),
                pe=20.0,
            ),
            CompanyMetrics(
                security_id=sid,
                as_of=date(2026, 7, 1),
                cik=1,
                price=12.0,
                period_end=date(2026, 3, 31),
                pe=24.0,
            ),  # after the date
            CusipMapping(cusip="AERO00001", security_id=sid),
            position(10, date(2025, 12, 31), date(2026, 2, 10), 1000),
            position(10, date(2026, 3, 31), date(2026, 5, 10), 1500),
            position(11, date(2026, 3, 31), date(2026, 5, 12), 100),
            position(11, date(2026, 6, 30), date(2026, 8, 1), 200),  # not filed yet
            # Settled 06-15, published 8 business days later (06-25); 06-30 isn't out yet.
            ShortInterest(security_id=sid, settlement_date=date(2026, 5, 29), short_position=100.0),
            ShortInterest(
                security_id=sid,
                settlement_date=date(2026, 6, 15),
                short_position=150.0,
                days_to_cover=2.0,
            ),
            ShortInterest(security_id=sid, settlement_date=date(2026, 6, 30), short_position=900.0),
            FundHolding(
                fund="ITA",
                period=date(2026, 3, 31),
                identifier="AERO00001",
                series_id="S1",
                accession="n1",
                filed=date(2026, 5, 20),
                cusip="AERO00001",
                weight=4.5,
            ),
            FilingText(
                accession="k1",
                section="risk_factors",
                cik=1,
                form="10-K",
                filed=date(2026, 2, 1),
                text="Risks.",
            ),
            FilingText(
                accession="q2",
                section="mdna",
                cik=1,
                form="10-Q",
                filed=date(2026, 8, 1),
                text="Later.",
            ),
            GraphNode(key="company:1", kind="company", label="Aero (AERO)"),
            GraphNode(key="theme:defense", kind="theme", label="Defense"),
            GraphNode(key="theme:critical_minerals", kind="theme", label="Minerals"),
            GraphEdge(src="company:1", dst="theme:defense", kind="part_of", source="themes"),
            GraphEdge(
                src="theme:critical_minerals",
                dst="theme:defense",
                kind="supplies",
                source="bea",
                buyer_share=0.05,
                supplier_share=0.1,
            ),
        ]
    )
    session.commit()


def test_company_dossier_is_point_in_time(session):
    setup(session)
    d = dossier.company(session, 1, AS_OF)
    assert d["identity"]["ticker"] == "AERO" and d["identity"]["sic"] == 3721
    assert d["metrics"]["pe"] == 20.0
    own = d["ownership"]
    assert own["period"] == date(2026, 3, 31) and own["holders"] == 2
    assert own["holders_change"] == 1 and own["shares_change"] == 600
    assert own["top"][0]["manager"] == "Big Fund"
    si = d["short_interest"]
    assert si["settlement_date"] == date(2026, 6, 15) and si["change"] == 0.5
    assert d["index_funds"] == [
        {"fund": "ITA", "period": date(2026, 3, 31), "weight": 4.5, "value": None}
    ]
    assert [s["accession"] for s in d["filing_text"]] == ["k1"]
    assert isinstance(d["timeline"], list)
    (theme,) = d["themes"]
    assert theme["theme"] == "theme:defense"
    assert theme["suppliers"][0]["node"] == "theme:critical_minerals"


def test_theme_dossier(session):
    setup(session)
    d = dossier.theme(session, "defense", AS_OF)
    assert d["companies"]["count"] == 1
    assert d["companies"]["largest"][0]["ticker"] == "AERO"
    assert d["supply_chain"]["suppliers"][0]["buyer_share"] == 0.05
    # No prices or lobbying loaded: each series reports its own error, the rest still works.
    assert d["series"]["ITA 3m return"]["spec"] == "px:ITA|ret:63"
    assert "error" in d["series"]["ITA 3m return"]
    assert isinstance(d["timeline"], list)


def test_api(session, engine):
    setup(session)

    def override():
        with session_factory(engine)() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        client = TestClient(app)
        body = client.get("/dossier/company/AERO", params={"as_of": "2026-06-30"}).json()
        assert body["metrics"]["pe"] == 20.0
        assert client.get("/dossier/company/NOPE").status_code == 404
        assert client.get("/dossier/theme/defense").json()["theme"]["name"] == "defense"
        assert client.get("/dossier/theme/nope").status_code == 404
    finally:
        app.dependency_overrides.clear()
