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
    assert stats == {"ordinary": 1, "adr": 1, "adr_valued": 1}
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
