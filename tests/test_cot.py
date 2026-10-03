from datetime import date, timedelta

import pytest
import respx
from sqlalchemy import func, select
from test_timeseries import add_bars

from fin_intel import cot, ingest, timeseries
from fin_intel.models import CotPosition
from fin_intel.providers import CftcProvider
from fin_intel.rebuild import rebuild

CFTC = "https://publicreporting.cftc.gov/resource"


def legacy_row(day, code="067651", noncomm=(361351, 251888), oi=1878576):
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "cftc_contract_market_code": code,
        "market_and_exchange_names": "WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE",
        "open_interest_all": str(oi),
        "noncomm_positions_long_all": str(noncomm[0]),
        "noncomm_positions_short_all": str(noncomm[1]),
        "noncomm_postions_spread_all": "563738",  # sic: the API's spelling
        "comm_positions_long_all": "873637",
        "comm_positions_short_all": "1016255",
        "nonrept_positions_long_all": "79850",
        "nonrept_positions_short_all": "46695",
    }


def disagg_row(day, mm=(209028, 129436), code="067651"):
    return {
        "report_date_as_yyyy_mm_dd": f"{day}T00:00:00.000",
        "cftc_contract_market_code": code,
        "open_interest_all": "1878576",
        "prod_merc_positions_long": "615887",
        "prod_merc_positions_short": "296348",
        "swap_positions_long_all": "113558",
        "swap__positions_short_all": "575715",
        "swap__positions_spread_all": "144192",
        "m_money_positions_long_all": str(mm[0]),
        "m_money_positions_short_all": str(mm[1]),
        "m_money_positions_spread": "302987",
        "other_rept_positions_long": "152323",
        "other_rept_positions_short": "122452",
        "other_rept_positions_spread": "260751",
        "nonrept_positions_long_all": "79850",
        "nonrept_positions_short_all": "46695",
    }


def test_parse_maps_irregular_field_names():
    rows = {r["group"]: r for r in cot.parse("disaggregated", [disagg_row("2026-09-29")])}
    assert set(rows) == {"producer", "swap", "managed_money", "other", "nonreportable"}
    assert (rows["swap"]["long"], rows["swap"]["short"], rows["swap"]["spread"]) == (
        113558,
        575715,
        144192,
    )
    assert rows["managed_money"]["report_date"] == date(2026, 9, 29)
    legacy = {r["group"]: r for r in cot.parse("legacy", [legacy_row("2026-09-29")])}
    assert legacy["noncommercial"]["spread"] == 563738
    assert legacy["commercial"]["spread"] is None


def test_resolve_picks_the_report_for_each_group():
    assert cot.resolve("crude", "managed_money") == ("067651", "disaggregated")
    assert cot.resolve("crude", "commercial") == ("067651", "legacy")
    assert cot.resolve("sp500", "leveraged") == ("13874A", "tff")
    assert cot.resolve("13874a", "nonreportable") == ("13874A", "tff")
    with pytest.raises(ValueError, match="unknown group"):
        cot.resolve("gold", "leveraged")  # a financial-futures group
    with pytest.raises(ValueError, match="unknown COT market"):
        cot.resolve("lumber", "commercial")


def test_cot_index_is_a_trailing_percentile(session):
    start = date(2023, 1, 3)
    nets = [float(i % 60) for i in range(160)]  # sawtooth between 0 and 59
    cot.load(
        session,
        "disaggregated",
        [disagg_row(start + timedelta(weeks=i), mm=(n, 0)) for i, n in enumerate(nets)],
    )
    rows = cot.history(session, "crude", "managed_money")
    index = cot.values(rows, "index")
    assert index[50] is None  # under a year of history
    assert index[59] == 100.0  # the top of the range so far
    assert index[60] == 0.0  # back to the bottom
    assert index[90] == pytest.approx(100 * 30 / 59)
    assert cot.values(rows, "net_pct_oi")[59] == pytest.approx(100 * 59 / 1878576)


@respx.mock
def test_sync_pages_then_rebuild(session, raw_store, monkeypatch):
    monkeypatch.setattr(CftcProvider, "page_size", 2)
    pages = [
        [legacy_row("2026-09-15"), legacy_row("2026-09-22")],
        [legacy_row("2026-09-29")],
    ]
    route = respx.get(f"{CFTC}/6dca-aqww.json").mock(
        side_effect=[respx.MockResponse(200, json=p) for p in pages]
    )
    cftc = CftcProvider(raw_store=raw_store)
    assert ingest.sync_cot(session, cftc, "legacy", date(2026, 9, 1)) == 9
    first, second = (c.request.url.params for c in route.calls)
    assert "'067651'" in first["$where"] and "'13874A'" in first["$where"]  # every market
    assert "report_date_as_yyyy_mm_dd >= '2026-09-01'" in first["$where"]
    assert (first["$offset"], second["$offset"]) == ("0", "2")

    before = session.scalars(select(CotPosition.long).order_by(CotPosition.report_date)).all()
    assert rebuild(session, raw_store, "cot") == {"cftc/legacy": 2}
    after = session.scalars(select(CotPosition.long).order_by(CotPosition.report_date)).all()
    assert after == before
    assert session.scalar(select(func.count()).select_from(CotPosition)) == 9


def test_timeseries_shows_positions_from_their_release(session):
    add_bars(session, "SPY", date(2026, 9, 21), [100.0] * 14)  # Mon 21 Sep to Sun 4 Oct
    cot.load(
        session,
        "legacy",
        [
            legacy_row("2026-09-22", noncomm=(100, 40)),  # Tuesday, released Friday the 25th
            legacy_row("2026-09-29", noncomm=(100, 10)),
        ],
    )
    spec = "cot:crude:noncommercial"
    days, pit = timeseries.build(session, [spec])
    by_day = dict(zip(days, pit[spec], strict=True))
    assert by_day[date(2026, 9, 24)] is None
    assert by_day[date(2026, 9, 25)] == 60
    assert by_day[date(2026, 9, 29)] == 60  # Tuesday's report isn't out yet
    assert by_day[date(2026, 10, 2)] == 90
    _, latest = timeseries.build(session, [spec], pit=False)
    assert dict(zip(days, latest[spec], strict=True))[date(2026, 9, 29)] == 90
    with pytest.raises(timeseries.SpecError, match="unknown group"):
        timeseries.build(session, ["cot:crude:dealer"])
