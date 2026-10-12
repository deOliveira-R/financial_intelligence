import io
from datetime import date

import openpyxl
import respx

from fin_intel import commodities
from fin_intel.models import EconomicSeries
from fin_intel.providers import WorldBankProvider
from fin_intel.providers.worldbank import PAGE

FILE = (
    "https://thedocs.worldbank.org/en/doc/abc-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
)


def pinksheet(rows):
    book = openpyxl.Workbook()
    book.active.title = "Mismatch Details"
    sheet = book.create_sheet("Monthly Prices")
    sheet.append(["World Bank Commodity Price Data (The Pink Sheet)"])
    sheet.append(["Updated on October 02, 2026"])
    sheet.append([None, "Crude oil, Brent", "Copper", "Gold", "Platinum"])
    sheet.append([None, "($/bbl)", "($/mt)", "($/troy oz)", "($/troy oz)"])
    for row in rows:
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


@respx.mock
def test_pinksheet_sync_and_point_in_time(session, raw_store):
    respx.get(PAGE).respond(text=f'<a href="{FILE}">Monthly prices</a>')
    respx.get(FILE).respond(
        content=pinksheet(
            [
                ["2026M07", 83.4, 13900.0, 3300.0, 1000.0],
                ["2026M08", 90.9, 14326.0, "…", 1010.0],
                ["2026M09", 116.8, 14474.0, 3400.0, 1020.0],
            ]
        )
    )
    rows = commodities.sync(session, WorldBankProvider(raw_store=raw_store))
    assert rows == 8  # Brent, copper and gold; the missing gold value and platinum skipped
    session.commit()
    assert session.get(EconomicSeries, "WB_GOLD").units == "$/troy oz"
    days = [date(2026, 8, 31), date(2026, 9, 5), date(2026, 9, 6), date(2026, 10, 1)]
    known = commodities._spec(session, "copper", days, pit=True)
    assert known == [13900.0, 13900.0, 14326.0, 14326.0]  # August's average from Sept 6
    final = commodities._spec(session, "copper", days, pit=False)
    assert final == [14326.0, 14326.0, 14326.0, 14474.0]  # dated by the month's end
    assert commodities.resolve("WB_GOLD") == "WB_GOLD"
