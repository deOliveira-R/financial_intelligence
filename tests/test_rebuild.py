import httpx
import pytest
import respx
from conftest import COMPANY_FACTS, FRED_OBS, FRED_SERIES, TICKERS, tiingo_bar
from sqlalchemy import select

from fin_intel import ingest
from fin_intel.models import (
    Concept,
    CorporateAction,
    DailyBar,
    EconomicObservation,
    Fact,
    Filing,
    FiscalCalendar,
    Security,
    TickerHistory,
)
from fin_intel.providers import FredProvider, SecProvider, TiingoProvider
from fin_intel.rebuild import TARGETS, rebuild

SNAPSHOT_TABLES = [
    Security,
    TickerHistory,
    Filing,
    Concept,
    Fact,
    FiscalCalendar,
    DailyBar,
    CorporateAction,
    EconomicObservation,
]


def snapshot(session):
    out = {}
    for model in SNAPSHOT_TABLES:
        cols = [c for c in model.__table__.columns]
        rows = session.execute(select(*cols)).all()
        out[model.__tablename__] = sorted(map(tuple, rows), key=repr)
    return out


@pytest.fixture
def synced(session, raw_store):
    """A database filled by live syncs (mocked HTTP), with raw responses captured."""
    with respx.mock:
        respx.get("https://www.sec.gov/files/company_tickers_exchange.json").respond(json=TICKERS)
        respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").respond(
            json=COMPANY_FACTS
        )
        respx.get("https://api.tiingo.com/tiingo/daily/SPY").respond(
            json={"ticker": "spy", "name": "SPDR S&P 500", "exchangeCode": "NYSE ARCA"}
        )
        respx.get("https://api.tiingo.com/tiingo/daily/SPY/prices").mock(
            side_effect=[
                httpx.Response(200, json=[tiingo_bar("2026-09-01", 600)]),
                httpx.Response(
                    200, json=[tiingo_bar("2026-09-01", 600), tiingo_bar("2026-09-02", 601, 2.0)]
                ),
            ]
        )
        respx.get("https://api.stlouisfed.org/fred/series").respond(json=FRED_SERIES)
        respx.get("https://api.stlouisfed.org/fred/series/observations").respond(json=FRED_OBS)
        sec = SecProvider(raw_store=raw_store)
        tiingo = TiingoProvider(raw_store=raw_store)
        ingest.sync_tickers(session, sec)
        ingest.sync_fundamentals(session, sec, "AAPL")
        ingest.sync_prices(session, tiingo, "SPY")  # not in SEC's list: created via metadata
        ingest.sync_prices(session, tiingo, "SPY")
        ingest.sync_economic(session, FredProvider(raw_store=raw_store), "UNRATE")
    return session


@pytest.mark.parametrize("target", list(TARGETS))
def test_rebuild_reproduces_tables_without_network(synced, raw_store, target):
    before = snapshot(synced)
    with respx.mock:  # any HTTP call would fail: respx rejects unmocked requests
        rebuild(synced, raw_store, target)
    assert snapshot(synced) == before


def test_rebuild_picks_up_parser_changes(synced, raw_store, monkeypatch):
    # Simulate a parser fix: labels now come from somewhere else. A rebuild applies it to
    # stored data with no refetch.
    from fin_intel.providers import sec

    original = sec.parse_company_facts

    def patched(cik, payload):
        filings, concepts, facts = original(cik, payload)
        for c in concepts:
            c["label"] = c["label"].upper() if c["label"] else None
        return filings, concepts, facts

    monkeypatch.setattr(sec, "parse_company_facts", patched)
    rebuild(synced, raw_store, "fundamentals")
    assert synced.scalar(select(Concept.label).where(Concept.name == "Revenues")) == "REVENUES"
