from datetime import date

import pytest
from test_timeseries import add_bars

from fin_intel import screentest
from fin_intel.models import CompanyMetrics, Issuer

START = date(2026, 1, 1)


def test_picks_vs_universe_with_a_delisting(session):
    add_bars(session, "SPY", START, [100.0] * 10)
    # A cheap winner, a cheap stock that stops trading (delisted) after falling, a dear one.
    win = add_bars(session, "WIN", START, [10.0, 10.0] + [11.0] * 8)
    bust = add_bars(session, "BUST", START, [10.0, 10.0, 5.0])
    dear = add_bars(session, "DEAR", START, [10.0] * 10)
    for i, (security, pe) in enumerate(((win, 5.0), (bust, 6.0), (dear, 40.0)), start=1):
        session.add(Issuer(cik=i, name=security.ticker))
        session.flush()
        security.cik = i
        session.add(
            CompanyMetrics(
                security_id=security.id,
                as_of=START,
                cik=i,
                price=10.0,
                period_end=date(2025, 12, 31),
                pe=pe,
            )
        )
    session.commit()

    r = screentest.run(session, filters=["pe<10"], sort="pe", top=5, horizons=(3, 50))
    (period,) = r.periods
    assert period.picks == ["WIN", "BUST"]
    # Bought Jan 2's close (10), held 3 days: WIN +10%, BUST stopped at 5 (-50%).
    assert period.returns[3] == pytest.approx((0.10 - 0.50) / 2)
    assert period.universe[3] == pytest.approx((0.10 - 0.50 + 0.0) / 3)
    assert period.spy[3] == 0.0 and period.returns[50] is None  # not over yet
    three, fifty = r.summary
    assert three.periods == 1 and three.beat_universe == 0.0
    assert fifty.periods == 0 and fifty.mean_return is None


def test_medians_and_bad_prices(session):
    add_bars(session, "SPY", START, [100.0] * 10)
    a = add_bars(session, "A", START, [10.0, 10.0, 10.0, 10.0, 200.0])  # one outlier (+1900%)
    b = add_bars(session, "B", START, [10.0, 10.0, 0.0, 0.0, 0.0])  # zero closes: ignored
    c = add_bars(session, "C", START, [10.0, 10.0, 11.0, 11.0, 11.0])
    for i, security in enumerate((a, b, c), start=1):
        session.add(Issuer(cik=i, name=security.ticker))
        session.flush()
        security.cik = i
        session.add(
            CompanyMetrics(
                security_id=security.id,
                as_of=START,
                cik=i,
                price=10.0,
                period_end=date(2025, 12, 31),
                pe=float(i),
            )
        )
    session.commit()
    r = screentest.run(session, filters=["pe<10"], sort="pe", top=3, horizons=(3,))
    (p,) = r.periods
    assert p.returns[3] == pytest.approx((19.0 + 0.0 + 0.1) / 3)  # B keeps its last good price
    assert p.median_return[3] == pytest.approx(0.1)
