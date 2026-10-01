from datetime import date

import pytest
import respx
from conftest import (
    MASSIVE_DIVIDENDS,
    MASSIVE_GROUPED,
    MASSIVE_SPLITS_PAGE1,
    MASSIVE_SPLITS_PAGE2,
    TICKERS,
)
from sqlalchemy import select

from fin_intel import ingest
from fin_intel.models import CorporateAction, DailyBar, RawResponse, Security
from fin_intel.providers import MassiveProvider
from fin_intel.providers.massive import normalize_symbol, parse_splits

API = "https://api.massive.com"


@pytest.mark.parametrize(
    ("massive", "ours"),
    [("AAPL", "AAPL"), ("BRK.B", "BRK-B"), ("JPMpC", "JPM-PC"), ("BACpL", "BAC-PL")],
)
def test_normalize_symbol(massive, ours):
    assert normalize_symbol(massive) == ours


def test_split_ratios_include_reverse_splits():
    ratios = [r["value"] for r in parse_splits(MASSIVE_SPLITS_PAGE2)]
    assert ratios == [50.0, 0.1]


@respx.mock
def test_grouped_daily_loads_known_securities(session, raw_store):
    ingest.load_company_tickers(session, TICKERS, date(2026, 1, 1))
    session.commit()
    route = respx.get(f"{API}/v2/aggs/grouped/locale/us/market/stocks/2026-09-29").respond(
        json=MASSIVE_GROUPED
    )

    rows = ingest.sync_market_daily(
        session, MassiveProvider(raw_store=raw_store), date(2026, 9, 29)
    )

    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer massive-key"
    assert request.url.params["adjusted"] == "false"
    assert rows == 2  # AAPL and BRK-B; the unknown warrant is skipped
    bars = session.execute(
        select(Security.ticker, DailyBar.close, DailyBar.source)
        .join(Security, Security.id == DailyBar.security_id)
        .order_by(Security.ticker)
    ).all()
    assert bars == [("AAPL", 255.5, "massive"), ("BRK-B", 481.0, "massive")]


@respx.mock
def test_actions_follow_pagination_with_auth_header(session, raw_store):
    ingest.load_company_tickers(session, TICKERS, date(2026, 1, 1))
    session.commit()
    first = respx.get(f"{API}/stocks/v1/splits", params={"limit": "5000"}).respond(
        json=MASSIVE_SPLITS_PAGE1
    )
    second = respx.get(f"{API}/stocks/v1/splits", params={"cursor": "abc"}).respond(
        json=MASSIVE_SPLITS_PAGE2
    )
    respx.get(f"{API}/stocks/v1/dividends").respond(json=MASSIVE_DIVIDENDS)
    massive = MassiveProvider(raw_store=raw_store)

    assert ingest.sync_market_actions(session, massive, "splits", date(2000, 1, 1)) == 2
    assert ingest.sync_market_actions(session, massive, "dividends", date(2000, 1, 1)) == 1

    assert first.calls[0].request.url.params["execution_date.gte"] == "2000-01-01"
    assert second.calls[0].request.headers["Authorization"] == "Bearer massive-key"
    actions = session.execute(
        select(Security.ticker, CorporateAction.action, CorporateAction.value)
        .join(Security, Security.id == CorporateAction.security_id)
        .order_by(CorporateAction.ex_date)
    ).all()
    assert actions == [("BRK-B", "split", 50.0), ("AAPL", "split", 4.0), ("AAPL", "dividend", 0.26)]
    pages = session.scalars(
        select(RawResponse.dataset)
        .where(RawResponse.provider == "massive")
        .order_by(RawResponse.id)
    )
    assert list(pages) == ["splits", "splits", "dividends"]
