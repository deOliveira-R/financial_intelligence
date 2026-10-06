from datetime import date

import pytest
import respx
from sqlalchemy import select

from fin_intel import ingest, putcall
from fin_intel.models import EconomicObservation
from fin_intel.providers import CboeProvider
from fin_intel.rebuild import rebuild

ARCHIVE = b"""Cboe Volume and Put/Call Ratio data is compiled for the convenience of visitors,,,,
, PRODUCT: EQUITY,,EXCHANGE: Cboe,
DATE,CALL,PUT,TOTAL,P/C Ratio
11/1/2006,976510,623929,1600439,0.64
10/4/2019,1000,500,1500,0.50
"""


def daily(calls, puts):
    product = [{"name": "VOLUME", "call": calls, "put": puts, "total": calls + puts}]
    return {
        "ratios": [{"name": "TOTAL PUT/CALL RATIO", "value": "0.50"}],
        "SUM OF ALL PRODUCTS": product,
        "EQUITY OPTIONS": product,
        "INDEX OPTIONS": product,
    }


def test_parsers():
    obs = {
        (o["series_id"], o["date"]): o["value"] for o in putcall.parse_archive("equity", ARCHIVE)
    }
    assert obs[("CBOE_EQUITY_PC", date(2006, 11, 1))] == pytest.approx(623929 / 976510)
    assert obs[("CBOE_EQUITY_CALLS", date(2019, 10, 4))] == 1000
    day = {
        (o["series_id"]): o["value"]
        for o in putcall.parse_daily(date(2026, 10, 2), daily(200, 100))
    }
    assert day["CBOE_TOTAL_PC"] == 0.5 and "CBOE_SPX_PC" not in day
    assert putcall.resolve("equity_pc") == "CBOE_EQUITY_PC"


@respx.mock
def test_sync_loads_archive_once_skips_holidays_and_rebuilds(session, raw_store, monkeypatch):
    monkeypatch.setattr(putcall, "DAILY_START", date(2026, 10, 1))
    base = "https://cdn.cboe.com"
    archive = respx.get(url__regex=rf"{base}/resources/.*pc\.csv").respond(content=ARCHIVE)
    respx.get(f"{base}/data/us/options/market_statistics/daily/2026-10-01_daily_options").respond(
        json=daily(100, 80)
    )
    respx.get(f"{base}/data/us/options/market_statistics/daily/2026-10-02_daily_options").respond(
        403, text="<Error><Code>AccessDenied</Code></Error>"
    )
    cboe = CboeProvider(raw_store=raw_store)
    ingest.sync_cboe(session, cboe, date(2026, 10, 3))
    assert archive.call_count == 3

    def pc():
        return dict(
            session.execute(
                select(EconomicObservation.date, EconomicObservation.value).where(
                    EconomicObservation.series_id == "CBOE_TOTAL_PC"
                )
            ).all()
        )

    assert pc()[date(2026, 10, 1)] == 0.8 and date(2026, 10, 2) not in pc()
    ingest.sync_cboe(session, cboe, date(2026, 10, 3))
    assert archive.call_count == 3  # archive once; only new days afterwards
    before = pc()
    with respx.mock:
        rebuild(session, raw_store, "economic")
    assert pc() == before
