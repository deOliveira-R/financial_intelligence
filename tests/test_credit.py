from datetime import date

import respx

from fin_intel import credit
from fin_intel.providers import BisProvider

TC_HEAD = (
    "FREQ,BORROWERS_CTY,TC_BORROWERS,TC_LENDERS,VALUATION,UNIT_TYPE,TC_ADJUST,COLLECTION,"
    "DECIMALS,UNIT_MULT,UNIT_MEASURE,TITLE_TS,TIME_PERIOD,OBS_VALUE,OBS_STATUS,OBS_PRE_BREAK,"
    "OBS_CONF"
)


def tc_row(unit, period, value, borrowers="P"):
    return (
        f"Q,US,{borrowers},A,M,{unit},A,E,1,0,USD,United States - Credit to Private "
        f"non-financial sector,{period},{value},A,,F"
    )


def quarters(n, start=2020):
    return [f"{start + i // 4}-Q{i % 4 + 1}" for i in range(n)]


def test_quarter_ends():
    assert credit.quarter_end("2026-Q1") == date(2026, 3, 31)
    assert credit.quarter_end("2025-Q4") == date(2025, 12, 31)
    assert credit.quarter_end("2025") is None


@respx.mock
def test_credit_impulse_and_point_in_time(session, raw_store):
    # Credit grows 10 a quarter for two years, then 20 a quarter: new credit over a year
    # rises from 40 to 80, an impulse of +40 on GDP of 1,000 = +4% of GDP.
    levels, level = [], 1000.0
    for i in range(12):
        level += 10 if i < 8 else 20
        levels.append(level)
    rows = [tc_row("XDC", q, v) for q, v in zip(quarters(12), levels, strict=True)]
    rows += [tc_row("770", q, v / 1000 * 100) for q, v in zip(quarters(12), levels, strict=True)]
    respx.get(url__regex=r"https://stats.bis.org/api/v2/data/dataflow/BIS/WS_TC/.*").respond(
        content="\n".join([TC_HEAD, *rows]).encode()
    )
    for flow in ("WS_DSR", "WS_CREDIT_GAP"):
        respx.get(url__regex=rf"https://stats.bis.org/api/v2/data/dataflow/BIS/{flow}/.*").respond(
            content=b"FREQ,BORROWERS_CTY\n"
        )
    assert credit.sync_bis(session, BisProvider(raw_store=raw_store)) == 24
    assert credit.countries(session) == ["US"]
    impulse = credit.history(session, "US", "credit_impulse")
    # 2022-Q1..Q4: each year-on-year flow compared with the year before it.
    assert [q for q, _ in impulse] == [
        date(2022, 3, 31), date(2022, 6, 30), date(2022, 9, 30), date(2022, 12, 31)
    ]  # fmt: skip
    first_gdp = levels[8] / (levels[8] / 1000 * 100 / 100)
    assert abs(impulse[0][1] - (50 - 40) / first_gdp * 100) < 1e-9
    assert abs(impulse[-1][1] - (80 - 40) / 1000 * 100) < 1e-9

    # Known only PUBLICATION_LAG after the quarter ends.
    days = [date(2022, 12, 31), date(2023, 6, 30)]
    pit = credit._spec(session, "US:credit_impulse", days, pit=True)
    assert pit[0] is None or pit[0] != impulse[-1][1]
    assert pit[1] == impulse[-1][1]

    # A new release replaces the dataset.
    credit.load(session, "WS_TC", "\n".join([TC_HEAD, tc_row("770", "2020-Q1", 99)]).encode())
    session.commit()
    assert credit.history(session, "US", "private_credit_gdp") == [(date(2020, 3, 31), 99.0)]


def jst_workbook(rows):
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["year", "country", "iso", "ifs", "gdp", "tloans", "crisisJST", "peg_type"])
    for r in rows:
        ws.append(r)
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@respx.mock
def test_macrohistory_panel(session, raw_store):
    from fin_intel import macrohistory
    from fin_intel.providers.macrohistory import JST_URL, MacrohistoryProvider

    host = "https://files.example/JSTdatasetR6.xlsx"
    respx.get(JST_URL).respond(status_code=302, headers={"location": host})
    respx.get(host).respond(
        content=jst_workbook(
            [
                [2007, "USA", "USA", 111, 14450.0, 12000.0, 1, "BASE"],
                [2008, "USA", "USA", 111, 14700.0, 12500.0, 0, "BASE"],
                [2008, "Japan", "JPN", 158, 500000.0, None, 0, "BASE"],
            ]
        )
    )
    rows = macrohistory.sync_jst(session, MacrohistoryProvider(raw_store=raw_store))
    assert rows == 8  # numeric values only: text and blanks are skipped
    data = macrohistory.panel(session, ["gdp", "crisisJST"])
    assert data[("USA", 2007)] == {"gdp": 14450.0, "crisisJST": 1.0}
    assert data[("JPN", 2008)] == {"gdp": 500000.0, "crisisJST": 0.0}


@respx.mock
def test_unfollowed_redirects_are_errors(raw_store):
    import pytest

    from fin_intel.providers import BisProvider
    from fin_intel.providers.errors import ProviderError

    respx.get(url__regex=r"https://stats.bis.org/.*").respond(
        status_code=302, headers={"location": "https://elsewhere.example/file"}
    )
    with pytest.raises(ProviderError, match="redirect"):
        BisProvider(raw_store=raw_store).fetch_dataset("WS_TC", "2.0")
