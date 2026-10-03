from datetime import date, timedelta

import pytest
import respx
from test_aliases import reference
from test_reference import ref

from fin_intel import ingest, metrics
from fin_intel.metrics import Item
from fin_intel.models import Security
from fin_intel.providers import MassiveProvider
from fin_intel.rebuild import rebuild

D = date.fromisoformat


def details(ticker, shares, figi=None):
    return {
        "results": {
            "ticker": ticker,
            "type": "ADRC",
            "composite_figi": figi,
            "share_class_shares_outstanding": shares,
        }
    }


def year_items(net_income, eps, shares):
    s, e = D("2025-01-01"), D("2025-12-31")
    return {
        "revenue": [Item(s, e, "FY", net_income * 5)],
        "net_income": [Item(s, e, "FY", net_income)],
        "eps_diluted": [Item(s, e, "FY", eps)],
        "shares_outstanding": [Item(e, e, "FY", shares)],
    }


def test_adr_market_cap_uses_depositary_shares():
    # 200M ordinary shares, 2 per ADS: 100M ADS at $6 is a $600M company, not $1.2B.
    items = year_items(60e6, 0.3, 200e6)
    adr = metrics._issuer_metrics(items, 6.0, D("2026-03-01"), listing_shares=100e6)
    assert adr["market_cap"] == pytest.approx(600e6)
    assert adr["pe"] == pytest.approx(10.0)
    unknown = metrics._issuer_metrics(items, 6.0, D("2026-03-01"), listing_shares=0.0)
    assert unknown["market_cap"] is None and unknown["pe"] is None


def test_adr_shares_need_a_recent_count():
    today = D("2026-10-01")
    adr = Security(ticker="MOMO", security_type="ADRC", shares_outstanding=1e8)
    assert metrics._adr_shares(adr, today) == 0.0  # never fetched
    adr.shares_as_of = today - timedelta(days=30)
    assert metrics._adr_shares(adr, today) == 1e8
    adr.shares_as_of = today - timedelta(days=200)
    assert metrics._adr_shares(adr, today) == 0.0
    assert metrics._adr_shares(Security(ticker="X", security_type="CS"), today) is None


@respx.mock
def test_sync_rotates_through_adrs_and_rebuilds(session, raw_store, monkeypatch):
    massive = MassiveProvider(raw_store=raw_store)
    reference(
        [
            ref("MOMO", "ADRC", cik=1610601, figi="BBG000MOMO01", mic="XNAS"),
            ref("NVO", "ADRC", cik=353278, figi="BBG000NVO001", mic="XNYS"),
            ref("AAPL", "CS", cik=320193, figi="BBG000AAPL01"),
        ]
    )
    ingest.sync_reference_tickers(session, massive)
    monkeypatch.setattr(ingest, "_today", lambda: date.today())
    assert ingest.due_listing_shares(session) == ["MOMO", "NVO"]  # ADRs only

    respx.get("https://api.massive.com/v3/reference/tickers/MOMO").respond(
        json=details("MOMO", 107565168, "BBG000MOMO01")
    )
    assert ingest.sync_listing_shares(session, massive, "MOMO") == 1
    momo = ingest.get_security(session, "MOMO")
    assert momo.shares_outstanding == 107565168 and momo.shares_as_of == date.today()
    assert ingest.due_listing_shares(session) == ["NVO"]  # MOMO is fresh now

    rebuild(session, raw_store, "market")
    assert ingest.get_security(session, "MOMO").shares_outstanding == 107565168
