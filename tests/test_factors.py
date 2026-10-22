import io
import zipfile
from datetime import date

import pytest
import respx
from sqlalchemy import select
from test_timeseries import add_bars

from fin_intel import factors, timeseries
from fin_intel.models import EconomicObservation
from fin_intel.providers import KenFrenchProvider
from fin_intel.rebuild import rebuild

THREE = """This file was created by using the 202608 CRSP database.
The Tbill return is the simple daily rate.

,Mkt-RF,SMB,HML,RF
20260105,    0.09,   -0.23,   -0.28,    0.01
20260106,    0.45,   -0.34,   -0.03,    0.01
20260107,  -99.99,    0.29,   -0.38,    0.01

Copyright 2026 Eugene F. Fama and Kenneth R. French
"""
FIVE = """Preamble

,Mkt-RF,SMB,HML,RMW,CMA,RF
20260106,    0.45,   -0.30,   -0.03,    0.10,    0.20,    0.01

Copyright 2026
"""
MOM = """Preamble

,Mom
20260106,   -1.47
"""


def zipped(name: str, text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name.replace("_CSV.zip", ".csv"), text)
    return buf.getvalue()


def test_parse_skips_the_preamble_and_stops_at_the_blank_line():
    rows = factors.parse_file("F-F_Research_Data_Factors_daily_CSV.zip", zipped("x_CSV.zip", THREE))
    got = {(r["series_id"], r["date"]): r["value"] for r in rows}
    assert got[("FF_MKT_RF", date(2026, 1, 5))] == 0.09
    assert got[("FF_RF", date(2026, 1, 6))] == 0.01
    assert ("FF_MKT_RF", date(2026, 1, 7)) not in got  # -99.99 is missing
    assert got[("FF_SMB", date(2026, 1, 7))] == 0.29
    assert factors.resolve("mkt-rf") == "FF_MKT_RF" and factors.resolve("Mom") == "FF_MOM"
    with pytest.raises(ValueError):
        factors.resolve("beta")


@respx.mock
def test_sync_loads_five_factor_values_over_three_and_serves_specs(session, raw_store):
    base = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
    files = {
        "F-F_Research_Data_Factors_daily_CSV.zip": THREE,
        "F-F_Research_Data_5_Factors_2x3_daily_CSV.zip": FIVE,
        "F-F_Momentum_Factor_daily_CSV.zip": MOM,
    }
    for name, text in files.items():
        respx.get(f"{base}/{name}").respond(content=zipped(name, text))
    factors.sync_factors(session, KenFrenchProvider(raw_store=raw_store))

    def value(series_id, day):
        return session.scalar(
            select(EconomicObservation.value).where(
                EconomicObservation.series_id == series_id, EconomicObservation.date == day
            )
        )

    assert value("FF_SMB", date(2026, 1, 6)) == -0.30  # the five-factor file wins
    assert value("FF_SMB", date(2026, 1, 5)) == -0.23  # only the three-factor file has 1926-1963
    assert value("FF_MOM", date(2026, 1, 6)) == -1.47
    assert value("FF_RMW", date(2026, 1, 6)) == 0.10

    add_bars(session, "SPY", date(2026, 1, 5), [100, 101, 102, 103])
    session.commit()
    days, series = timeseries.build(session, ["ff:mkt_rf"], date(2026, 1, 5), date(2026, 1, 8))
    assert series["ff:mkt_rf"] == [None, 0.09, 0.45, None]  # known the next day
    _, final = timeseries.build(
        session, ["ff:mkt_rf"], date(2026, 1, 5), date(2026, 1, 8), pit=False
    )
    assert final["ff:mkt_rf"][0] == 0.09
    with respx.mock:
        rebuild(session, raw_store, "economic")
    assert value("FF_SMB", date(2026, 1, 6)) == -0.30
