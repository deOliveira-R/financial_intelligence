from datetime import date

import respx
from sqlalchemy import select

from fin_intel import putcall, volatility
from fin_intel.models import EconomicObservation
from fin_intel.providers import CboeProvider
from fin_intel.rebuild import rebuild

VIX = b"""DATE,OPEN,HIGH,LOW,CLOSE
01/02/1990,17.240000,17.240000,17.240000,17.240000
01/03/1990,18.190000,18.190000,18.190000,18.190000
bad row,,,,
"""
PUT = b"""DATE,PUT
03/04/1991,153.500000
03/05/1991,154.100000
"""


def test_parse_both_file_layouts():
    vix = volatility.parse_history("VIX", VIX)
    assert [(o["date"], o["value"]) for o in vix] == [
        (date(1990, 1, 2), 17.24),
        (date(1990, 1, 3), 18.19),
    ]
    assert {o["series_id"] for o in vix} == {"CBOE_VIX"}
    put = volatility.parse_history("put", PUT)
    assert put[-1] == {"series_id": "CBOE_PUT", "date": date(1991, 3, 5), "value": 154.1}
    assert volatility.parse_history("VIX", b"") == []


def test_resolution_shares_the_cboe_source():
    assert putcall.resolve("vix") == "CBOE_VIX"
    assert putcall.resolve("put") == "CBOE_PUT"
    assert putcall.resolve("vix_pc") == "CBOE_VIX_PC"  # VIX options volume, not the index
    assert volatility.resolve("nothing") is None


@respx.mock
def test_sync_loads_histories_and_rebuilds(session, raw_store):
    base = "https://cdn-api.cboe.com/api/global/us_indices/daily_prices"
    respx.get(f"{base}/VIX_History.csv").respond(content=VIX)
    respx.get(f"{base}/PUT_History.csv").respond(content=PUT)
    cboe = CboeProvider(raw_store=raw_store)
    assert volatility.sync_volatility(session, cboe, ("VIX", "PUT")) == 4

    def closes():
        return dict(
            session.execute(
                select(EconomicObservation.date, EconomicObservation.value).where(
                    EconomicObservation.series_id == "CBOE_PUT"
                )
            ).all()
        )

    assert closes() == {date(1991, 3, 4): 153.5, date(1991, 3, 5): 154.1}
    before = closes()
    with respx.mock:
        rebuild(session, raw_store, "economic")
    assert closes() == before
