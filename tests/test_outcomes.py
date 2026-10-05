from datetime import date

import pytest
from sqlalchemy import select
from test_timeseries import add_bars

from fin_intel import outcomes
from fin_intel.models import CompanyMetrics, ForwardOutcome, Issuer

START = date(2026, 1, 1)


def snapshot(session, security, cik, sic, pe, as_of=START):
    if session.get(Issuer, cik) is None:
        session.add(Issuer(cik=cik, name=security.ticker, sic=sic))
        session.flush()
    security.cik = cik
    session.add(
        CompanyMetrics(
            security_id=security.id,
            as_of=as_of,
            cik=cik,
            price=10.0,
            period_end=date(2025, 12, 31),
            pe=pe,
        )
    )


def test_outcomes_against_spy_universe_and_sector(session, monkeypatch):
    monkeypatch.setattr(outcomes, "HORIZONS", {"1m": 2, "3m": 3, "6m": 4, "12m": 5})
    add_bars(session, "SPY", START, [100.0] * 9)
    up = add_bars(session, "UP", START, [10, 10, 11, 12, 13, 14, 15, 15, 15])
    flat = add_bars(session, "FLAT", START, [10.0] * 9)
    bust = add_bars(session, "BUST", START, [10, 10, 8, 5])  # stops trading
    for i, (s, sic) in enumerate(((up, 3674), (flat, 3674), (bust, 6022)), start=1):
        snapshot(session, s, i, sic, pe=20.0)
    session.commit()

    assert outcomes.compute(session) == 3
    got = {o.cik: o for o in session.scalars(select(ForwardOutcome))}
    # Bought at Jan 2's close (10); 3 trading days later.
    assert got[1].ret_3m == pytest.approx(0.3) and got[3].ret_3m == pytest.approx(-0.5)
    assert got[1].excess_spy_3m == pytest.approx(0.3)
    assert got[1].excess_universe_3m == pytest.approx(0.3 - 0.0)  # median of 0.3, 0, -0.5
    assert got[1].sector == "manufacturing" and got[1].excess_sector_3m is None  # < 5 peers
    assert got[3].max_drawdown_12m == pytest.approx(-0.5)  # keeps its last price
    assert got[1].ret_12m == pytest.approx(0.5)
