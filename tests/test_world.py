import json
from datetime import date

from sqlalchemy import select

from fin_intel import esef, ingest, taiwan, world
from fin_intel.models import Issuer, StatementItem


def test_issuer_ids_are_stable_and_disjoint():
    assert world.issuer_id("edinet", "E00776") == 10_000_000_776
    assert world.issuer_id("dart", "00126380") == 20_000_126_380
    assert world.issuer_id("twse", "2330") == 30_000_002_330
    lei = world.issuer_id("esef", "724500Y6DUVHQD6OXN27")
    assert lei == world.issuer_id("esef", "724500Y6DUVHQD6OXN27") and lei >= 10**12


TSMC_IS = {
    "出表日期": "1151004",
    "年度": "115",
    "季別": "2",
    "公司代號": "2330",
    "營業收入": "2404483690.00",
    "營業利益（損失）": "1425568793.00",
    "淨利（淨損）歸屬於母公司業主": "1279041690.00",
    "基本每股盈餘（元）": "49.33",
}
TSMC_BS = {
    "出表日期": "1151004",
    "年度": "115",
    "季別": "2",
    "公司代號": "2330",
    "資產總計": "9375654727.00",
    "股本": "259323701.00",
    "母公司暨子公司所持有之母公司庫藏股股數（單位：股）": "0.00",
}
BANK_IS = {
    "Year": "115",
    "Season": "2",
    "SecuritiesCompanyCode": "5880",
    "利息淨收益": "100",
    "利息以外淨損益": "40",
}


def test_taiwan_tables_are_year_to_date_in_thousands():
    income = taiwan.parse_table([TSMC_IS, BANK_IS], "income", date(2026, 10, 4))
    revenue = next(f for f in income["2330"] if f["concept"] == "Revenue")
    assert (revenue["start"], revenue["end"]) == (date(2026, 1, 1), date(2026, 6, 30))
    assert revenue["value"] == 2404483690000.0 and revenue["fp"] == "Q2"
    eps = next(f for f in income["2330"] if f["concept"] == "EPS")
    assert eps["value"] == 49.33 and eps["unit"] == "TWD/shares"
    bank = next(f for f in income["5880"] if f["concept"] == "Revenue")
    assert bank["value"] == 140_000  # net interest plus other net revenue
    balance = taiwan.parse_table([TSMC_BS], "balance", date(2026, 10, 4))
    shares = next(f for f in balance["2330"] if f["concept"] == "SharesOutstanding")
    assert shares["value"] == 25_932_370_100  # capital / TWD 10 par


def test_taiwan_balance_sheet_loads(session):
    rows = ingest.load_twse_table(session, "twse|t187ap07_ci|2026-10-04", [TSMC_BS])
    assert rows > 0
    cik = world.issuer_id("twse", "2330")
    assert session.get(Issuer, cik).country == "TW"
    items = dict(
        session.execute(
            select(StatementItem.line_item, StatementItem.value).where(StatementItem.cik == cik)
        ).all()
    )
    assert items["total_assets"] == 9375654727000.0


def esef_report() -> bytes:
    def fact(concept, period, unit, value, **dims):
        return {
            "value": value,
            "dimensions": {
                "concept": concept,
                "entity": "scheme:LEI",
                "period": period,
                "unit": unit,
                **dims,
            },
        }

    year = "2025-01-01T00:00:00/2026-01-01T00:00:00"
    facts = {
        "f1": fact(
            "ifrs-full:RevenueFromContractsWithCustomers", year, "iso4217:EUR", "32667300000"
        ),
        "f2": fact("ifrs-full:Assets", "2026-01-01T00:00:00", "iso4217:EUR", "55576800000"),
        "f3": fact(
            "ifrs-full:BasicEarningsLossPerShare", year, "iso4217:EUR/xbrli:shares", "26.29"
        ),
        "f4": fact(
            "ifrs-full:RevenueFromContractsWithCustomers",
            year,
            "iso4217:EUR",
            "1",
            **{"ifrs-full:SegmentsAxis": "asml:SystemsMember"},
        ),
        "f5": fact("asml:Bookings", year, "iso4217:EUR", "5"),
    }
    return json.dumps({"documentInfo": {}, "facts": facts}).encode()


def test_esef_periods_end_the_day_before_midnight():
    p = esef.parse_report(esef_report(), "esef:x", date(2026, 2, 11))
    facts = p["facts"]["ifrs-full"]
    (revenue,) = facts["RevenueFromContractsWithCustomers"]["units"]["EUR"]  # no segment
    assert (revenue["start"], revenue["end"], revenue["val"]) == (
        "2025-01-01",
        "2025-12-31",
        32667300000.0,
    )
    assert facts["Assets"]["units"]["EUR"][0]["end"] == "2025-12-31"
    assert "EUR/shares" in facts["BasicEarningsLossPerShare"]["units"]
    assert revenue["fy"] == 2025 and set(facts) >= {"Assets"}


def test_esef_report_loads(session):
    key = "724500Y6DUVHQD6OXN27|2025-12-31|NL|2026-02-11|ASML Holding N.V."
    assert ingest.load_esef_report(session, key, esef_report()) > 0
    cik = world.issuer_id("esef", "724500Y6DUVHQD6OXN27")
    issuer = session.get(Issuer, cik)
    assert (issuer.name, issuer.country, issuer.lei) == (
        "ASML Holding N.V.",
        "NL",
        "724500Y6DUVHQD6OXN27",
    )
    revenue = session.scalar(
        select(StatementItem.value).where(
            StatementItem.cik == cik, StatementItem.line_item == "revenue"
        )
    )
    assert revenue == 32667300000.0


def test_taiwan_prices_create_listings_and_value_in_dollars(session):
    from datetime import date as d

    from fin_intel import metrics
    from fin_intel.models import CompanyMetrics, EconomicObservation, EconomicSeries, Security

    world.ensure_issuer(session, "twse", "2330", "TSMC", home_ticker="2330")
    session.commit()
    full_year = dict(TSMC_IS, 季別="4", 年度="114")  # FY2025, so there's an annual figure
    full_year["淨利（淨損）歸屬於母公司業主"] = "1695124900.00"
    ingest.load_twse_table(session, "twse|t187ap06_ci|2026-03-31", [full_year])
    ingest.load_twse_table(session, "twse|t187ap07_ci|2026-10-04", [TSMC_BS])
    day = d.today()
    quote = {
        "tables": [
            {
                "fields": [
                    "證券代號",
                    "證券名稱",
                    "成交股數",
                    "開盤價",
                    "最高價",
                    "最低價",
                    "收盤價",
                ],
                "data": [
                    [
                        "2330",
                        "台積電",
                        "15,792,206",
                        "2,505.00",
                        "2,515.00",
                        "2,495.00",
                        "2,500.00",
                    ],
                    ["0050", "元大台灣50", "1", "1", "1", "1", "1"],  # an ETF: no filings
                ],
            }
        ]
    }
    assert ingest.load_tw_prices(session, f"twse|{day.isoformat()}", quote) == 1
    tsmc = session.scalar(select(Security).where(Security.ticker == "2330.TW"))
    assert (tsmc.currency, tsmc.mic, tsmc.cik) == ("TWD", "XTAI", world.issuer_id("twse", "2330"))
    session.add(EconomicSeries(id="DEXTAUS", source="fred"))
    session.flush()
    session.add(EconomicObservation(series_id="DEXTAUS", date=day, value=32.0))
    session.commit()

    metrics.compute(session)
    row = session.scalar(select(CompanyMetrics).where(CompanyMetrics.security_id == tsmc.id))
    assert row.price == 2500.0 / 32  # USD
    assert row.currency == "TWD"
    # 25.93B shares x $78.13 = $2.03T; net income TWD 1.695T = $53B: P/E ~38
    assert 37 < row.pe < 39
