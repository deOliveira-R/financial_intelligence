import csv
import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from fin_intel import importers, portfolio
from fin_intel.api import app
from fin_intel.db import get_session, session_factory
from fin_intel.models import (
    Account,
    CorporateAction,
    DailyBar,
    PortfolioTransaction,
    Security,
)
from fin_intel.portfolio import Tx, build_lots, holding_term

D = date.fromisoformat


def tx(i, day, action, qty=None, price=None, amount=None, symbol="ABC", account=1, fees=0.0):
    return Tx(i, account, D(day), action, symbol, qty, price, amount, fees)


# --- lot engine ------------------------------------------------------------------------


def test_fifo_sale_spans_lots_with_correct_terms():
    book = build_lots(
        [
            tx(1, "2024-01-02", "buy", 10, 100),
            tx(2, "2024-06-03", "buy", 10, 150),
            tx(3, "2025-03-03", "sell", 15, 120),
        ]
    )
    first, second = book.realized
    assert (first.quantity, first.term, first.gain) == (10, "long", pytest.approx(200))
    assert (second.quantity, second.term, second.gain) == (5, "short", pytest.approx(-150))
    (remaining,) = book.open
    assert (remaining.quantity, remaining.cost_basis) == (5, pytest.approx(750))


def test_long_term_means_more_than_one_year():
    assert holding_term(D("2024-01-02"), D("2025-01-02")) == "short"  # exactly one year
    assert holding_term(D("2024-01-02"), D("2025-01-03")) == "long"
    assert holding_term(D("2024-02-29"), D("2025-03-01")) == "short"
    assert holding_term(D("2024-02-29"), D("2025-03-02")) == "long"


def test_amount_and_fees_set_cost_and_proceeds():
    book = build_lots(
        [
            tx(1, "2025-01-02", "buy", 10, 100, fees=5),  # cost = 1000 + 5
            tx(2, "2025-02-03", "sell", 10, amount=1095),  # broker-reported net proceeds
        ]
    )
    (r,) = book.realized
    assert (r.cost_basis, r.proceeds, r.gain) == (1005, 1095, 90)


def test_market_split_applies_unless_broker_recorded_it():
    splits = {"ABC": [(D("2025-06-02"), 4.0)]}
    market = build_lots([tx(1, "2025-01-02", "buy", 10, 400)], splits)
    assert market.open[0].quantity == 40 and market.open[0].cost_per_share == 100

    broker = build_lots(
        [tx(1, "2025-01-02", "buy", 10, 400), tx(2, "2025-06-02", "split", 30)], splits
    )
    assert broker.open[0].quantity == 40  # not 160: the market split is ignored


def test_selling_more_than_history_shows_is_flagged():
    book = build_lots([tx(1, "2025-01-02", "buy", 5, 10), tx(2, "2025-02-03", "sell", 8, 12)])
    assert any("exceeds" in w for w in book.warnings)
    unmatched = [r for r in book.realized if r.acquired is None]
    assert unmatched[0].quantity == pytest.approx(3) and unmatched[0].gain is None


def test_transfer_in_without_basis_stays_unknown():
    book = build_lots([tx(1, "2025-01-02", "transfer_in", 10)])
    assert book.open[0].cost_basis is None
    assert any("no cost basis" in w for w in book.warnings)


@pytest.mark.parametrize(
    ("rebuy_day", "account", "risky"),
    [
        ("2025-03-20", 1, True),  # 19 days after the loss sale
        ("2025-03-31", 1, True),  # 30 days after
        ("2025-04-01", 1, False),  # 31 days after
        ("2025-03-20", 2, True),  # any account counts, IRAs included
    ],
)
def test_wash_sale_flag(rebuy_day, account, risky):
    book = build_lots(
        [
            tx(1, "2025-01-02", "buy", 10, 100),
            tx(2, "2025-03-01", "sell", 10, 80),
            tx(3, rebuy_day, "buy", 10, 82, account=account),
        ]
    )
    loss = next(r for r in book.realized if r.sold == D("2025-03-01"))
    assert loss.wash_sale_risk is risky


def test_gains_are_never_wash_sales():
    book = build_lots(
        [
            tx(1, "2025-01-02", "buy", 10, 100),
            tx(2, "2025-03-01", "sell", 10, 120),
            tx(3, "2025-03-05", "buy", 10, 121),
        ]
    )
    assert not book.realized[0].wash_sale_risk


# --- database-backed -------------------------------------------------------------------


@pytest.fixture
def accounts(session):
    session.add_all(
        [
            Account(
                id=1,
                name="Fidelity Individual",
                broker="fidelity",
                account_type="taxable",
                taxable=True,
            ),
            Account(
                id=2,
                name="Fidelity Roth",
                broker="fidelity",
                account_type="roth_ira",
                taxable=False,
            ),
            Account(
                id=3, name="Vanguard 401k", broker="vanguard", account_type="401k", taxable=False
            ),
        ]
    )
    session.commit()


def csv_rows(text):
    return list(csv.DictReader(io.StringIO(text.strip())))


TODAY = date.today()


def days_ago(n):
    return (TODAY - timedelta(days=n)).isoformat()


def test_reimporting_overlapping_exports_is_idempotent(session, accounts):
    header = "account,date,action,symbol,quantity,price,amount,fees,description\n"
    first = header + (
        "Fidelity Individual,2025-01-02,buy,ABC,10,100,-1000,0,\n"
        # Two genuinely identical trades on one day: both count.
        "Fidelity Individual,2025-01-03,buy,ABC,1,101,-101,0,\n"
        "Fidelity Individual,2025-01-03,buy,ABC,1,101,-101,0,\n"
    )
    longer = first + "Fidelity Individual,2025-02-03,sell,ABC,2,110,220,0,\n"
    assert importers.load_transactions(session, csv_rows(first), "generic") == 3
    assert importers.load_transactions(session, csv_rows(longer), "generic") == 1
    assert session.scalar(select(func.count()).select_from(PortfolioTransaction)) == 4


def test_unknown_account_is_rejected(session, accounts):
    rows = csv_rows(
        "account,date,action,symbol,quantity,price,amount,fees\nNope,2025-01-02,buy,ABC,1,1,,\n"
    )
    with pytest.raises(importers.PortfolioImportError, match="unknown account"):
        importers.load_transactions(session, rows, "generic")


def add_security(session, ticker, closes, security_type="CS", start=None):
    security = Security(ticker=ticker, origin="massive", security_type=security_type)
    session.add(security)
    session.flush()
    start = start or TODAY - timedelta(days=len(closes) - 1)
    session.add_all(
        DailyBar(
            security_id=security.id,
            date=start + timedelta(days=i),
            source="massive",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=1_000_000,
        )
        for i, c in enumerate(closes)
    )
    session.commit()
    return security


def test_harvest_candidates_and_wash_sale_blockers(session, accounts):
    add_security(session, "ABC", [100.0, 80.0])
    add_security(session, "XYZ", [50.0, 60.0])
    rows = [
        {
            "account": "Fidelity Individual",
            "date": days_ago(400),
            "action": "buy",
            "symbol": "ABC",
            "quantity": "10",
            "price": "100",
        },
        {
            "account": "Fidelity Individual",
            "date": days_ago(400),
            "action": "buy",
            "symbol": "XYZ",
            "quantity": "10",
            "price": "50",
        },  # a gain: not a candidate
        {
            "account": "Fidelity Roth",
            "date": days_ago(10),
            "action": "buy",
            "symbol": "ABC",
            "quantity": "2",
            "price": "85",
        },  # tax-advantaged: not a candidate, but it blocks
    ]
    importers.load_transactions(session, rows, "generic")

    (candidate,) = portfolio.harvest_candidates(session)
    assert candidate.lot.account == "Fidelity Individual" and candidate.lot.term == "long"
    assert (candidate.loss, candidate.loss_pct) == (pytest.approx(200), pytest.approx(0.2))
    assert [(a, q) for a, _, q in candidate.blocking_purchases] == [("Fidelity Roth", 2.0)]
    assert candidate.rebuy_after == TODAY + timedelta(days=31)
    assert portfolio.harvest_candidates(session, min_loss=500) == []


def test_positions_reconcile_with_broker_snapshot(session, accounts):
    add_security(session, "ABC", [100.0])
    importers.load_transactions(
        session,
        [
            {
                "account": "Fidelity Individual",
                "date": "2025-01-02",
                "action": "buy",
                "symbol": "ABC",
                "quantity": "10",
                "price": "90",
            }
        ],
        "generic",
    )
    importers.load_positions(
        session,
        [
            {
                "account": "Fidelity Individual",
                "as_of": TODAY.isoformat(),
                "symbol": "ABC",
                "quantity": "12",
            },
            # A 401(k) trust with no ticker or market data: priced from the broker.
            {
                "account": "Vanguard 401k",
                "as_of": TODAY.isoformat(),
                "symbol": "VTR2050TRUST",
                "quantity": "100",
                "price": "55.5",
            },
        ],
    )
    by_symbol = {p.symbol: p for p in portfolio.positions(session)}
    abc = by_symbol["ABC"]
    assert (abc.market_value, abc.unrealized, abc.reconciled, abc.broker_quantity) == (
        1000,
        100,
        False,
        12,
    )
    assert by_symbol["VTR2050TRUST"].quantity == 0  # held per broker, no transactions yet
    assert portfolio.latest_prices(session, {"VTR2050TRUST"})["VTR2050TRUST"][2] == "broker"


def test_market_split_from_corporate_actions(session, accounts):
    security = add_security(session, "SPLT", [400.0, 100.0], start=D("2025-06-01"))
    session.add(
        CorporateAction(
            security_id=security.id,
            ex_date=D("2025-06-02"),
            action="split",
            source="massive",
            value=4.0,
        )
    )
    session.commit()
    importers.load_transactions(
        session,
        [
            {
                "account": "Fidelity Individual",
                "date": "2025-01-02",
                "action": "buy",
                "symbol": "SPLT",
                "quantity": "10",
                "price": "400",
            }
        ],
        "generic",
    )
    (lot,) = portfolio.lot_views(session)
    assert (lot.quantity, lot.cost_basis, lot.market_value) == (40, 4000, 4000)


def test_context_and_replacements(session):
    import math

    base = [100 * math.exp(0.01 * math.sin(i / 3) + 0.0005 * i) for i in range(300)]
    add_security(session, "SPY", base, "ETF")
    add_security(session, "IVV", [x * 1.001 for x in base], "ETF")  # tracks SPY
    add_security(session, "GLD", [200 + (i % 7) for i in range(300)], "ETF")  # unrelated
    ctx = portfolio.context(session, "IVV")
    assert ctx.high_52w >= ctx.price >= ctx.low_52w and ctx.returns["1y"] is not None
    assert ctx.spy_returns["1m"] == pytest.approx(ctx.returns["1m"], abs=1e-9)
    add_security(session, "SPXL", [100 * (x / base[0]) ** 3 for x in base], "ETF")  # 3x
    ranked = portfolio.replacements(session, "SPY", min_dollar_volume=0)
    assert [r.ticker for r in ranked][:2] == ["IVV", "SPXL"]
    ivv, spxl = ranked[0], ranked[1]
    assert ivv.correlation > 0.99 and ivv.beta == pytest.approx(1.0, abs=0.01)
    assert spxl.beta > 2.5 and spxl.tracking_error > 10 * ivv.tracking_error  # leverage sinks it


def test_portfolio_api(engine, session, accounts):
    add_security(session, "ABC", [100.0, 80.0])
    importers.load_transactions(
        session,
        [
            {
                "account": "Fidelity Individual",
                "date": days_ago(400),
                "action": "buy",
                "symbol": "ABC",
                "quantity": "10",
                "price": "100",
            }
        ],
        "generic",
    )

    def override():
        with session_factory(engine)() as s:
            yield s

    app.dependency_overrides[get_session] = override
    try:
        client = TestClient(app)
        (position,) = client.get("/portfolio/positions").json()
        assert (position["symbol"], position["unrealized"]) == ("ABC", -200)
        (candidate,) = client.get("/portfolio/harvest").json()
        assert candidate["loss"] == 200
        assert [a["name"] for a in client.get("/portfolio/accounts").json()][
            0
        ] == "Fidelity Individual"
    finally:
        app.dependency_overrides.clear()
