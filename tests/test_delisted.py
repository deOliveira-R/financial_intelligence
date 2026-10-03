from datetime import date

import respx
from sqlalchemy import select

from fin_intel import ingest
from fin_intel.models import DailyBar, Security, TickerHistory
from fin_intel.providers import MassiveProvider
from fin_intel.rebuild import rebuild

API = "https://api.massive.com"
OLD_DAY, NEW_DAY = date(2025, 6, 2), date(2026, 1, 5)  # ABCD's old and new owners


def grouped(day, results):
    respx.get(f"{API}/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}").respond(
        json={"results": [{"T": t, "o": c, "h": c, "l": c, "c": c, "v": 100} for t, c in results]}
    )


def reference(active, delisted):
    respx.get(f"{API}/v3/reference/tickers", params={"active": "true"}).respond(
        json={"results": active}
    )
    respx.get(f"{API}/v3/reference/tickers", params={"active": "false"}).respond(
        json={"results": delisted}
    )


def ticker(symbol, cik=None, figi=None, delisted=None):
    row = {
        "ticker": symbol,
        "name": f"{symbol} Inc",
        "type": "CS",
        "primary_exchange": "XNAS",
        "cik": f"{cik:010d}" if cik else None,
        "composite_figi": figi,
    }
    if delisted:
        row["delisted_utc"] = f"{delisted.isoformat()}T00:00:00Z"
    return row


def bars(session, security_id):
    return session.execute(
        select(DailyBar.date, DailyBar.close)
        .where(DailyBar.security_id == security_id)
        .order_by(DailyBar.date)
    ).all()


def run(session, raw_store, active, delisted):
    """Market data first (as it arrived historically), then the reference sync."""
    massive = MassiveProvider(raw_store=raw_store)
    ingest.sync_market_daily(session, massive, OLD_DAY)
    ingest.sync_market_daily(session, massive, NEW_DAY)
    reference(active, delisted)
    ingest.sync_reference_tickers(session, massive)


@respx.mock
def test_delisted_security_gets_its_history_from_raw(session, raw_store):
    grouped(OLD_DAY, [("GONE", 5.0), ("SPY", 500.0)])
    grouped(NEW_DAY, [("SPY", 600.0)])
    run(session, raw_store, [ticker("SPY")], [ticker("GONE", cik=7, delisted=date(2025, 9, 1))])

    gone = ingest.get_security(session, "GONE")  # resolves through ticker history
    assert (gone.ticker, gone.active, gone.origin) == (None, False, "massive-delisted")
    assert gone.delisted_on == date(2025, 9, 1) and gone.cik == 7
    assert bars(session, gone.id) == [(OLD_DAY, 5.0)]


@respx.mock
def test_reused_symbol_splits_history_between_companies(session, raw_store):
    grouped(OLD_DAY, [("ABCD", 10.0)])  # the old company
    grouped(NEW_DAY, [("ABCD", 20.0)])  # the new one
    run(
        session,
        raw_store,
        [ticker("ABCD", cik=2, figi="BBG000NEW001")],
        [ticker("ABCD", cik=1, delisted=date(2025, 7, 1))],
    )
    new = session.scalar(select(Security).where(Security.ticker == "ABCD"))
    old = session.scalar(select(Security).where(Security.origin == "massive-delisted"))
    assert (new.cik, old.cik) == (2, 1)
    assert bars(session, old.id) == [(OLD_DAY, 10.0)]  # moved off the new holder
    assert bars(session, new.id) == [(NEW_DAY, 20.0)]


@respx.mock
def test_same_company_delisting_moves_nothing(session, raw_store):
    grouped(OLD_DAY, [("ABCD", 10.0)])
    grouped(NEW_DAY, [("ABCD", 11.0)])
    run(
        session,
        raw_store,
        [ticker("ABCD", cik=1)],
        [ticker("ABCD", cik=1, delisted=date(2026, 2, 1))],
    )
    (security,) = session.scalars(select(Security)).all()
    assert security.delisted_on == date(2026, 2, 1)
    assert bars(session, security.id) == [(OLD_DAY, 10.0), (NEW_DAY, 11.0)]


@respx.mock
def test_figi_match_is_a_rename(session, raw_store):
    grouped(OLD_DAY, [("OLDX", 10.0)])  # traded as OLDX before the rename
    grouped(NEW_DAY, [("NEWX", 12.0)])
    run(
        session,
        raw_store,
        [ticker("NEWX", figi="BBG000SAME01")],
        [ticker("OLDX", figi="BBG000SAME01", delisted=date(2025, 8, 1))],
    )
    (security,) = session.scalars(select(Security)).all()
    assert security.ticker == "NEWX"
    symbols = set(session.scalars(select(TickerHistory.ticker)))
    assert symbols == {"OLDX", "NEWX"}
    assert bars(session, security.id) == [(OLD_DAY, 10.0), (NEW_DAY, 12.0)]


@respx.mock
def test_resync_is_idempotent_and_rebuild_reproduces_it(session, raw_store):
    grouped(OLD_DAY, [("ABCD", 10.0), ("GONE", 5.0)])
    grouped(NEW_DAY, [("ABCD", 20.0)])
    active = [ticker("ABCD", cik=2, figi="BBG000NEW001")]
    delisted = [
        ticker("ABCD", cik=1, delisted=date(2025, 7, 1)),
        ticker("GONE", cik=7, delisted=date(2025, 9, 1)),
    ]
    run(session, raw_store, active, delisted)
    ingest.sync_reference_tickers(session, MassiveProvider(raw_store=raw_store))  # again

    def state():
        securities = sorted(
            (s.origin, s.cik, s.delisted_on, tuple(bars(session, s.id)))
            for s in session.scalars(select(Security))
        )
        return securities

    live = state()
    assert len(live) == 3
    with respx.mock:  # no network during the rebuild
        rebuild(session, raw_store, "all")
    assert state() == live
