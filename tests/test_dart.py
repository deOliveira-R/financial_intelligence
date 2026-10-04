from datetime import date

import respx
from sqlalchemy import select

from fin_intel import dart, ingest, world
from fin_intel.models import Issuer, StatementItem
from fin_intel.providers import DartProvider
from fin_intel.rebuild import rebuild

API = "https://opendart.fss.or.kr/api"


def row(sj, account, thstrm, frmtrm=None, add=None, frm_add=None, receipt="20260814003699"):
    return {
        "rcept_no": receipt,
        "sj_div": sj,
        "account_id": account,
        "currency": "KRW",
        "thstrm_amount": thstrm,
        "frmtrm_amount": frmtrm,
        "thstrm_add_amount": add,
        "frmtrm_add_amount": frm_add,
    }


HALF = {
    "status": "000",
    "list": [
        row("BS", "ifrs-full_Assets", "600,000", "566,942"),
        row("IS", "ifrs-full_Revenue", "171", "75", "305", "154"),
        row("CIS", "ifrs-full_Revenue", "999", "999", "999", "999"),  # IS wins
        row("IS", "dart_OperatingIncomeLoss", "89", "5", "147", "12"),
        row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "120", "40"),
        row("SCE", "ifrs-full_ProfitLoss", "1"),  # equity statement: skipped
        row("IS", "-표준계정코드 미사용-", "5"),  # non-standard account: skipped
        row("IS", "ifrs-full_BasicEarningsLossPerShare", "10,000"),
    ],
}


def facts_by(facts):
    out = {}
    for f in facts:  # the first fact per period wins, as in world.facts_payload
        out.setdefault((f["concept"], f["start"], f["end"]), f["value"])
    return out


def test_periods_follow_the_report_and_fiscal_year():
    facts = facts_by(dart.parse_statements(HALF, 2026, "11012"))
    d = date
    assert facts[("Assets", None, d(2026, 6, 30))] == 600_000
    assert facts[("Assets", None, d(2025, 12, 31))] == 566_942
    assert facts[("Revenue", d(2026, 4, 1), d(2026, 6, 30))] == 171  # the quarter
    assert facts[("Revenue", d(2026, 1, 1), d(2026, 6, 30))] == 305  # year to date
    assert facts[("Revenue", d(2025, 4, 1), d(2025, 6, 30))] == 75
    assert facts[("CashFlowsFromUsedInOperatingActivities", d(2026, 1, 1), d(2026, 6, 30))] == 120
    assert ("ProfitLoss", d(2026, 4, 1), d(2026, 6, 30)) not in facts
    eps = next(f for f in dart.parse_statements(HALF, 2026, "11012") if "PerShare" in f["concept"])
    assert eps["unit"] == "KRW/shares" and eps["filed"] == d(2026, 8, 14)
    # A March fiscal year: business year 2025 runs April 2025 to March 2026.
    assert dart.fiscal_year(2025, 3) == (d(2025, 4, 1), d(2026, 3, 31))


def test_periods_past_their_deadline_newest_first():
    periods = ingest.dart_periods(date(2026, 10, 4), first_year=2025)
    assert periods == [
        (2026, "11012"),
        (2026, "11013"),
        (2025, "11011"),
        (2025, "11014"),
        (2025, "11012"),
        (2025, "11013"),
    ]


@respx.mock
def test_report_sync_falls_back_to_separate_statements_and_rebuilds(session, raw_store):
    respx.get(f"{API}/fnlttSinglAcntAll.json", params={"fs_div": "CFS"}).respond(
        json={"status": "013", "message": "조회된 데이타가 없습니다."}
    )
    respx.get(f"{API}/fnlttSinglAcntAll.json", params={"fs_div": "OFS"}).respond(json=HALF)
    respx.get(f"{API}/stockTotqySttus.json").respond(
        json={
            "status": "000",
            "list": [
                {
                    "rcept_no": "20260814003699",
                    "se": "보통주",
                    "distb_stock_co": "5,919,637,922",
                    "stlm_dt": "2026-06-30",
                },
                {
                    "rcept_no": "20260814003699",
                    "se": "합계",
                    "distb_stock_co": "6,800,000,000",
                    "stlm_dt": "2026-06-30",
                },
            ],
        }
    )
    world.ensure_issuer(session, "dart", "00126380", "SAMSUNG", home_ticker="005930")
    session.commit()
    provider = DartProvider(raw_store=raw_store)
    assert ingest.sync_dart_report(session, provider, "00126380", 2026, "11012", shares=True) > 0
    cik = world.issuer_id("dart", "00126380")
    assert cik == 20_000_126_380 and session.get(Issuer, cik).country == "KR"

    def items():
        return dict(
            session.execute(
                select(StatementItem.line_item, StatementItem.value).where(
                    StatementItem.cik == cik,
                    StatementItem.period_end == date(2026, 6, 30),
                    StatementItem.period_start.in_([date(2026, 4, 1), date(2026, 6, 30)]),
                )
            ).all()
        )

    assert items() == {
        "total_assets": 600_000,
        "revenue": 171,
        "operating_income": 89,
        "shares_outstanding": 5_919_637_922,  # common shares only
    } | {k: v for k, v in items().items() if k == "eps_basic"}
    rebuild(session, raw_store, "fundamentals")
    assert items()["revenue"] == 171
