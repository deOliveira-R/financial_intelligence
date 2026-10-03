from datetime import date

import pytest
from fastapi.testclient import TestClient

from fin_intel import screener
from fin_intel.api import app
from fin_intel.db import get_session, session_factory
from fin_intel.models import CompanyMetrics, Issuer, Security

TODAY = date(2026, 10, 2)


def company(session, ticker, **metrics):
    cik = abs(hash(ticker)) % 10**9
    session.add(Issuer(cik=cik, name=ticker))
    session.flush()
    security = Security(ticker=ticker, name=f"{ticker} Inc", cik=cik, origin="sec")
    session.add(security)
    session.flush()
    defaults = {"market_cap": 5e9, "operating_margin_vs_5y": 0.0}
    session.add(
        CompanyMetrics(
            security_id=security.id,
            as_of=TODAY,
            cik=cik,
            price=10.0,
            period_end=date(2026, 6, 30),
            **{**defaults, **metrics},
        )
    )
    session.commit()


@pytest.fixture
def market(session):
    company(
        session,
        "CHEAP",
        pe=8.0,
        earnings_yield=0.15,
        roic=0.10,
        p_b=0.8,
        piotroski_f=8,
        current_ratio=2.0,
    )
    company(session, "GOOD", pe=25.0, earnings_yield=0.05, roic=0.40)
    company(session, "BOTH", pe=10.0, earnings_yield=0.16, roic=0.35)
    company(session, "PEAK", pe=4.0, earnings_yield=0.30, roic=0.50, operating_margin_vs_5y=0.25)
    company(session, "TINY", pe=5.0, earnings_yield=0.20, roic=0.20, market_cap=1e8)
    return session


def tickers(rows):
    return [r["ticker"] for r in rows]


def test_filters_and_sort(market):
    _, rows = screener.screen(market, ["pe<12", "market_cap>=1e9"], sort="pe")
    assert tickers(rows) == ["PEAK", "CHEAP", "BOTH"]
    _, rows = screener.screen(market, ["roic>=0.3"], sort="-roic")
    assert tickers(rows) == ["PEAK", "GOOD", "BOTH"]


def test_magic_formula_rank_and_peak_cycle_exclusion(market):
    as_of, rows = screener.screen(market, preset="magic")
    assert as_of == TODAY
    # PEAK (margins far above their 5-year average) and TINY (< $1B) are excluded.
    # Earnings-yield ranks BOTH 0, CHEAP 1, GOOD 2; ROIC ranks GOOD 0, BOTH 1, CHEAP 2.
    assert tickers(rows) == ["BOTH", "GOOD", "CHEAP"] and rows[0]["rank"] == 1


def test_deep_value_preset(market):
    _, rows = screener.screen(market, preset="deep_value")
    assert tickers(rows) == ["CHEAP"]


@pytest.mark.parametrize("bad", ["pe<<3", "nope<1", "pe<abc"])
def test_bad_filters(market, bad):
    with pytest.raises(screener.ScreenError):
        screener.screen(market, [bad])


def test_screener_api(engine, market):
    def override():
        with session_factory(engine)() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        client = TestClient(app)
        body = client.get("/screener", params={"where": ["pe<12"], "sort": "pe", "limit": 2}).json()
        assert body["count"] == 2 and [r["ticker"] for r in body["results"]] == ["PEAK", "TINY"]
        assert client.get("/screener", params={"preset": "nope"}).status_code == 400
    finally:
        app.dependency_overrides.clear()
