from dataclasses import replace
from datetime import date, timedelta

import pytest

from fin_intel import metrics
from fin_intel.metrics import Item, _consistent, _growth, _issuer_metrics, _ttm
from fin_intel.models import (
    CompanyMetrics,
    Concept,
    DailyBar,
    Issuer,
    Security,
    StatementItem,
)

D = date.fromisoformat


def q(start, end, fp, value):
    return Item(D(start), D(end), fp, value)


QUARTERS = [
    q("2025-01-01", "2025-03-31", "Q1", 10),
    q("2025-04-01", "2025-06-30", "Q2", 11),
    q("2025-07-01", "2025-09-30", "Q3", 12),
    q("2025-10-01", "2025-12-31", "Q4", 13),
    q("2026-01-01", "2026-03-31", "Q1", 14),
]


def test_ttm_uses_last_four_consecutive_quarters():
    assert _ttm(QUARTERS) == (11 + 12 + 13 + 14, D("2026-03-31"))
    assert _ttm(QUARTERS, as_of_end=D("2025-12-31")) == (46, D("2025-12-31"))


def test_ttm_falls_back_to_the_latest_fiscal_year():
    annual = [q("2024-01-01", "2024-12-31", "FY", 40), q("2025-01-01", "2025-12-31", "FY", 50)]
    assert _ttm(annual) == (50, D("2025-12-31"))
    gap = [QUARTERS[0], QUARTERS[1], QUARTERS[3], QUARTERS[4], *annual]  # Q3 missing
    assert _ttm(gap) == (50, D("2025-12-31"))
    newer_annual = [*QUARTERS[:4], q("2025-04-01", "2026-03-31", "FY", 60)]
    assert _ttm(newer_annual) == (60, D("2026-03-31"))


def test_growth_handles_sign_changes():
    assert _growth(120, 100) == pytest.approx(0.2)
    assert _growth(50, -100) == pytest.approx(1.5)  # from a loss to a profit
    assert _growth(10, 0) is None and _growth(None, 5) is None


def fy(end, value):
    return Item(D(end) - timedelta(days=364), D(end), "FY", value)


def test_share_class_consistency():
    # 10B shares, $50B net income, EPS $5, price $100: consistent.
    items = {"net_income": [fy("2025-12-31", 50e9)], "eps_diluted": [fy("2025-12-31", 5.0)]}
    assert _consistent(10e9, 100.0, items)
    # Berkshire-like: Class A count and basic EPS, Class B price (a "P/E" of 0.008).
    brk = {"net_income": [fy("2025-12-31", 90e9)], "eps_basic": [fy("2025-12-31", 62_000.0)]}
    assert not _consistent(1.45e6, 480.0, brk)
    # A count on another basis than EPS (e.g. one class's cover-page count only).
    assert not _consistent(3e9, 100.0, items)
    # Judged on the same period for both, the latest one reporting both.
    nvda_like = {
        "net_income": [fy("2026-01-25", 120e9), Item(D("2026-04-27"), D("2026-07-26"), "Q2", 60e9)],
        "eps_diluted": [fy("2026-01-25", 4.9), Item(D("2026-04-27"), D("2026-07-26"), "Q2", 2.46)],
    }
    assert _consistent(24.1e9, 230.0, nvda_like)
    assert _consistent(None, 1.0, items) and _consistent(1e9, 1.0, {})
    # Honeywell-like: shares halved after the fiscal year; the latest quarter agrees.
    hon = {
        "net_income": [
            fy("2025-12-31", 4.7e9),
            Item(D("2026-04-01"), D("2026-06-30"), "Q2", 5.7e9),
        ],
        "eps_diluted": [fy("2025-12-31", 7.36), Item(D("2026-04-01"), D("2026-06-30"), "Q2", 17.8)],
    }
    assert _consistent(317e6, 214.0, hon)
    # A loss or a cents-level EPS in the latest quarter: judged on an earlier period.
    tiny = {
        "net_income": [fy("2025-12-31", 50e9), Item(D("2026-01-01"), D("2026-03-31"), "Q1", 1e8)],
        "eps_diluted": [fy("2025-12-31", 5.0), Item(D("2026-01-01"), D("2026-03-31"), "Q1", 0.01)],
    }
    assert _consistent(10e9, 100.0, tiny) and not _consistent(3e9, 100.0, tiny)


def annual_items(year, revenue, ebit, net, assets, equity):
    s, e = D(f"{year}-01-01"), D(f"{year}-12-31")
    flow = lambda v: Item(s, e, "FY", v)  # noqa: E731
    stock = lambda v: Item(e, e, "FY", v)  # noqa: E731
    return {
        "revenue": [flow(revenue)],
        "operating_income": [flow(ebit)],
        "net_income": [flow(net)],
        "operating_cash_flow": [flow(net * 1.2)],
        "total_assets": [stock(assets)],
        "equity": [stock(equity)],
        "shares_outstanding": [stock(1e9)],
        "eps_diluted": [flow(net / 1e9)],
    }


def merge(*dicts):
    out = {}
    for d in dicts:
        for k, v in d.items():
            out.setdefault(k, []).extend(v)
    return out


def test_issuer_metrics_and_staleness():
    items = merge(
        annual_items(2024, 100e9, 15e9, 10e9, 200e9, 80e9),
        annual_items(2025, 120e9, 20e9, 14e9, 210e9, 90e9),
    )
    m = _issuer_metrics(items, price=200.0, as_of=D("2026-03-01"))
    assert m["market_cap"] == 200e9 and m["pe"] == pytest.approx(200e9 / 14e9)
    assert m["revenue_growth"] == pytest.approx(0.2)
    assert m["operating_margin"] == pytest.approx(20 / 120)
    assert m["piotroski_f"] is not None and m["piotroski_f"] >= 4
    assert _issuer_metrics(items, price=200.0, as_of=D("2028-01-01")) is None  # stale


def test_compute_picks_the_primary_security(session):
    session.add_all([Issuer(cik=1, name="Co"), Concept(id=1, taxonomy="us-gaap", name="X")])
    session.commit()
    common = Security(ticker="CO", cik=1, security_type="CS", mic="XNYS", origin="sec")
    warrant = Security(ticker="CO-WT", cik=1, security_type="WARRANT", mic="XNYS", origin="sec")
    session.add_all([common, warrant])
    session.flush()
    today = date.today()
    session.add_all(
        DailyBar(
            security_id=s.id, date=today - timedelta(days=1), source="massive", close=p, volume=1000
        )
        for s, p in ((common, 50.0), (warrant, 2.0))
    )
    for item, value, start in (
        ("revenue", 100e6, today - timedelta(days=365)),
        ("net_income", 10e6, today - timedelta(days=365)),
        ("shares_outstanding", 2e6, today - timedelta(days=1)),
    ):
        end = today - timedelta(days=1)
        session.add(
            StatementItem(
                cik=1,
                line_item=item,
                period_start=start,
                period_end=end,
                period_type="annual" if item != "shares_outstanding" else "instant",
                fiscal_period="FY",
                unit="USD",
                value=value,
                concept_id=1,
            )
        )
    session.commit()
    assert metrics.compute(session) == 1
    (row,) = session.query(CompanyMetrics).all()
    assert row.security_id == common.id and row.market_cap == 100e6 and row.pe == pytest.approx(10)


def test_foreign_filers_are_converted_to_dollars():
    def row(line_item, unit, end, value, fp="FY"):
        return (line_item, unit, Item(D(end) - timedelta(days=364), D(end), fp, value))

    rows = [
        row("revenue", "DKK", "2025-12-31", 700.0),
        row("net_income", "DKK", "2025-12-31", 70.0),
        row("eps_diluted", "DKK/shares", "2025-12-31", 7.0),
        row("shares_outstanding", "shares", "2025-12-31", 10.0),
        row("revenue", "EUR", "2020-12-31", 50.0),  # before a switch to DKK: dropped
    ]
    currency, items = metrics.in_dollars(rows, {"DKK": 0.15, "USD": 1.0})
    assert currency == "DKK"
    assert [i.value for i in items["revenue"]] == [pytest.approx(105.0)]
    assert items["eps_diluted"][0].value == pytest.approx(1.05)
    assert items["shares_outstanding"][0].value == 10.0  # counts aren't money

    # Without a rate, amounts stay as reported and valuation is skipped.
    currency, raw = metrics.in_dollars(rows, {"USD": 1.0})
    assert currency == "DKK" and raw["revenue"][0].value == 700.0
    m = _issuer_metrics(raw, 20.0, D("2026-03-01"), valued=False)
    assert m is not None and m["market_cap"] is None and m["pe"] is None
    assert m["net_margin"] == pytest.approx(0.1)  # ratios within the statements still work


def test_usd_rates_use_each_series_latest_value(session):
    from fin_intel import fx
    from fin_intel.config import get_settings
    from fin_intel.models import EconomicObservation, EconomicSeries

    for sid in ("DEXCHUS", "DEXUSEU"):
        session.add(EconomicSeries(id=sid, source="fred"))
    session.flush()
    session.add_all(
        [
            EconomicObservation(series_id="DEXCHUS", date=D("2026-09-30"), value=7.0),
            EconomicObservation(series_id="DEXCHUS", date=D("2026-10-01"), value=None),
            EconomicObservation(series_id="DEXCHUS", date=D("2026-09-29"), value=7.2),
            EconomicObservation(series_id="DEXUSEU", date=D("2026-10-01"), value=1.1),
        ]
    )
    session.flush()
    rates = fx.usd_rates(session)
    assert rates["CNY"] == pytest.approx(1 / 7.0)  # missing latest day skipped
    assert rates["EUR"] == pytest.approx(1.1)  # quoted as dollars per euro
    assert rates["USD"] == 1.0 and "BRL" not in rates
    assert "DEXBZUS" in get_settings().fred_series_ids  # synced with the macro pack


def test_split_factor_runs_both_ways():
    splits = [(D("2026-06-10"), 4.0)]
    assert metrics.split_factor(splits, D("2026-05-01"), D("2026-07-01")) == 4.0
    assert metrics.split_factor(splits, D("2026-07-01"), D("2026-05-01")) == 0.25
    assert metrics.split_factor(splits, D("2026-06-10"), D("2026-07-01")) == 1.0  # same day


def test_split_adjusted_matches_the_days_price():
    splits = [(D("2026-06-10"), 4.0)]
    shares = Item(D("2026-03-31"), D("2026-03-31"), "Q1", 1e9, filed=D("2026-05-01"))
    eps = Item(D("2026-01-01"), D("2026-03-31"), "Q1", 2.0, filed=D("2026-05-01"))
    out = metrics.split_adjusted(
        {"shares_outstanding": [shares], "eps_diluted": [eps], "revenue": []},
        splits,
        D("2026-07-01"),
    )
    assert out["shares_outstanding"][0].value == 4e9 and out["eps_diluted"][0].value == 0.5
    # A value restated after the split, viewed from before it, goes back to old terms.
    restated = replace(shares, value=4e9, filed=D("2026-08-01"))
    back = metrics.split_adjusted({"shares_outstanding": [restated]}, splits, D("2026-06-01"))
    assert back["shares_outstanding"][0].value == 1e9


def test_historical_compute_uses_what_was_known_then(session):
    session.add_all([Issuer(cik=1, name="Co"), Concept(id=1, taxonomy="us-gaap", name="X")])
    session.commit()
    gone = Security(ticker=None, cik=1, security_type="CS", mic="XNYS", origin="massive")
    gone.active = False  # delisted since: still part of the past universe
    session.add(gone)
    session.flush()
    as_of = D("2025-06-30")
    session.add(
        DailyBar(security_id=gone.id, date=D("2025-06-27"), source="massive", close=20.0, volume=1)
    )
    session.add(
        DailyBar(security_id=gone.id, date=D("2025-09-30"), source="massive", close=99.0, volume=1)
    )

    def add(item, end, value, first_filed, start=None, ptype="annual"):
        session.add(
            StatementItem(
                cik=1,
                line_item=item,
                period_start=start or end,
                period_end=end,
                period_type=ptype,
                fiscal_period="FY",
                unit="USD",
                value=value,
                concept_id=1,
                filed=D("2026-02-15"),
                first_filed=first_filed,
            )
        )

    add("revenue", D("2024-12-31"), 100e6, D("2025-02-15"), D("2024-01-01"))
    add("net_income", D("2024-12-31"), 10e6, D("2025-02-15"), D("2024-01-01"))
    add("shares_outstanding", D("2024-12-31"), 5e6, D("2025-02-15"), ptype="instant")
    add("net_income", D("2025-12-31"), 99e6, D("2026-02-15"), D("2025-01-01"))  # not yet known
    session.commit()

    assert metrics.compute(session, as_of) == 1
    (row,) = session.query(CompanyMetrics).all()
    assert row.as_of == as_of and row.price == 20.0  # that day's close, not later ones
    assert row.net_income_ttm == 10e6 and row.pe == pytest.approx(10)


def test_shares_implied_by_net_income_and_eps_when_untagged():
    items = {
        "revenue": [fy("2025-12-31", 30e9)],
        "net_income": [fy("2025-12-31", 10.2e9)],
        "eps_basic": [fy("2025-12-31", 26.0)],
    }
    assert metrics.implied_shares(items, D("2025-12-31")) == pytest.approx(10.2e9 / 26)
    m = _issuer_metrics(items, 900.0, D("2026-03-01"))
    assert m["market_cap"] == pytest.approx(900 * 10.2e9 / 26)
    assert m["pe"] == pytest.approx(900 / 26)
    loss = {"net_income": [fy("2025-12-31", -1e9)], "eps_basic": [fy("2025-12-31", 2.0)]}
    assert metrics.implied_shares(loss, D("2025-12-31")) is None  # inconsistent signs
