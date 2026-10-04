from datetime import date, timedelta

import pytest

from fin_intel import crosslist, metrics, world
from fin_intel.models import Concept, DailyBar, Issuer, Security, StatementItem

TODAY = date.today()


def test_names_and_ratios():
    adr = crosslist._tokens("SCHNEIDER ELECT SE-UNSP ADR")
    assert adr == ["schneider", "elect"]
    assert crosslist._same_name(adr, crosslist._tokens("SCHNEIDER ELECTRIC SE"))
    assert not crosslist._same_name(
        crosslist._tokens("ROCHE BOBOIS"), crosslist._tokens("ROCHE HOLDING AG")
    )
    assert (
        crosslist._nice(0.48) == 0.5 and crosslist._nice(9.7) == 10 and crosslist._nice(7.0) is None
    )


def bars(session, security, closes):
    session.add_all(
        DailyBar(
            security_id=security.id,
            date=TODAY - timedelta(days=i),
            source="massive",
            close=c,
            volume=1000,
        )
        for i, c in enumerate(closes)
    )


def test_link_ordinary_by_share_class_and_adr_by_name(session):
    cik = world.ensure_issuer(
        session, "esef", "969500HMVSZ0TCV65D58", "Schneider Electric SE", country="FR"
    )
    issuer = session.get(Issuer, cik)
    issuer.share_class_figi, issuer.figi_name = "BBG001S67MN2", "SCHNEIDER ELECTRIC SE"
    session.add(Concept(id=1, taxonomy="ifrs-full", name="NumberOfSharesOutstanding"))
    session.add(
        StatementItem(
            cik=cik,
            line_item="shares_outstanding",
            period_start=TODAY,
            period_end=TODAY,
            period_type="instant",
            unit="shares",
            value=560e6,
            concept_id=1,
        )
    )
    ordinary = Security(
        ticker="SBGSF",
        security_type="OS",
        origin="massive",
        share_class_figi="BBG001S67MN2",
        figi_name="SCHNEIDER ELECTRIC SE",
    )
    adr = Security(
        ticker="SBGSY",
        security_type="ADRC",
        origin="massive",
        share_class_figi="BBG001T2HYZ0",
        figi_name="SCHNEIDER ELECT SE-UNSP ADR",
    )
    other = Security(
        ticker="RHHBY",
        security_type="ADRC",
        origin="massive",
        figi_name="ROCHE HOLDINGS AG-SPONS ADR",
    )
    session.add_all([ordinary, adr, other])
    session.flush()
    bars(session, ordinary, [250.0] * 6)
    bars(session, adr, [50.1, 49.9, 50.0, 50.0, 50.0, 50.0])  # 1 ADR = 0.2 shares
    session.commit()

    stats = crosslist.link(session, TODAY)
    assert stats == {"ordinary": 1, "adr": 1, "adr_valued": 1, "same_as_sec": 0}
    assert ordinary.cik == cik and adr.cik == cik and other.cik is None
    assert adr.shares_outstanding == pytest.approx(560e6 / 0.2)

    # The ordinary line trades in USD per ordinary share: it prices the company.
    primary = metrics.primary_securities(session)
    assert primary[cik].ticker in ("SBGSF", "SBGSY")


def test_illiquid_us_line_doesnt_price_a_foreign_company(session):
    cik = world.ensure_issuer(session, "dart", "00126380", "SAMSUNG", home_ticker="005930")
    line = Security(ticker="SSNLF", security_type="OS", origin="massive", cik=cik)
    session.add(line)
    session.flush()
    bars(session, line, [60.0, 61.0])  # two trades in a month
    session.commit()
    assert cik not in metrics.primary_securities(session)


def test_isins_by_lei_reads_only_wanted_leis():
    import io
    import zipfile

    from fin_intel.providers.gleif import isins_by_lei

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("lei-isin.csv", "LEI,ISIN\nA1,FR0000121972\nA1,XS1234567890\nB2,US0000000001\n")
    assert isins_by_lei(buf.getvalue(), {"A1"}) == {"A1": ["FR0000121972", "XS1234567890"]}


def test_sync_maps_home_european_and_us_listings_then_links(session, raw_store):
    import io
    import json
    import zipfile

    import httpx
    import respx

    from fin_intel import ingest
    from fin_intel.providers import GleifProvider, OpenFigiProvider
    from fin_intel.rebuild import rebuild

    tel = world.ensure_issuer(session, "edinet", "E02652", "Tokyo Electron", home_ticker="8035")
    sch = world.ensure_issuer(
        session,
        "esef",
        "969500HMVSZ0TCV65D58",
        "Schneider",
        country="FR",
        lei="969500HMVSZ0TCV65D58",
    )
    toelf = Security(ticker="TOELF", security_type="OS", origin="massive", active=True)
    sbgsf = Security(ticker="SBGSF", security_type="OS", origin="massive", active=True)
    session.add_all([toelf, sbgsf])
    session.commit()

    def answer(request):
        jobs = json.loads(request.content)
        figis = {
            "8035": "BBG001S5QVC5",
            "FR0000121972": "BBG001S67MN2",
            "TOELF": "BBG001S5QVC5",
            "SBGSF": "BBG001S67MN2",
        }
        out = []
        for job in jobs:
            figi = figis.get(job["idValue"])
            out.append(
                {
                    "data": [
                        {
                            "shareClassFIGI": figi,
                            "name": job["idValue"],
                            "securityType": "Common Stock",
                        }
                    ]
                }
                if figi
                else {"warning": "No identifier found."}
            )
        return httpx.Response(200, json=out)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(
            "lei-isin.csv",
            "LEI,ISIN\n969500HMVSZ0TCV65D58,XS0000000001\n969500HMVSZ0TCV65D58,FR0000121972\n",
        )
    with respx.mock:
        respx.post("https://api.openfigi.com/v3/mapping").mock(side_effect=answer)
        respx.get("https://mapping.gleif.org/api/v2/isin-lei/latest/download").respond(
            content=buf.getvalue()
        )
        stats = ingest.sync_crosslist(
            session, OpenFigiProvider(raw_store=raw_store), GleifProvider(raw_store=raw_store)
        )
    assert stats["home"] == 1 and stats["isin"] == 1 and stats["us"] == 2
    assert stats["ordinary"] == 2
    assert (toelf.cik, sbgsf.cik) == (tel, sch)
    assert session.get(Issuer, sch).isin == "FR0000121972"
    # Replaying the stored mappings reproduces the links.
    rebuild(session, raw_store, "market")


def test_home_issuer_of_an_sec_filer_is_marked_and_skipped(session):
    from fin_intel.models import Issuer as I

    session.add(I(cik=1046179, name="TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD"))
    home = world.ensure_issuer(session, "twse", "2330", "TSMC", home_ticker="2330")
    issuer = session.get(I, home)
    issuer.share_class_figi, issuer.figi_name = "BBG001S6Q004", "TAIWAN SEMICONDUCTOR MANUFAC"
    adr = Security(
        ticker="TSM",
        security_type="ADRC",
        origin="sec",
        cik=1046179,
        figi_name="TAIWAN SEMICONDUCTOR-SP ADR",
        mic="XNYS",
    )
    listing = Security(
        ticker="2330.TW", security_type="CS", origin="twse", cik=home, mic="XTAI", currency="TWD"
    )
    session.add_all([adr, listing])
    session.flush()
    bars(session, adr, [300.0] * 6)
    bars(session, listing, [2500.0] * 6)
    session.commit()
    assert crosslist.link(session, TODAY)["same_as_sec"] == 1
    assert issuer.same_as == 1046179 and adr.cik == 1046179  # the ADR stays the SEC filer's
    primary = metrics.primary_securities(session)
    assert home not in primary and primary[1046179].ticker == "TSM"
