from datetime import date

from sqlalchemy import select

from fin_intel import ingest
from fin_intel.models import Issuer, Security, TickerHistory


def sync(session, rows, today):
    payload = {"fields": ["cik", "name", "ticker", "exchange"], "data": rows}
    ingest.load_company_tickers(session, payload, today)
    session.commit()


def test_rename_keeps_the_same_security(session):
    sync(session, [[1326801, "Facebook", "FB", "Nasdaq"]], date(2022, 6, 1))
    fb_id = ingest.get_security(session, "FB").id
    sync(session, [[1326801, "Meta Platforms", "META", "Nasdaq"]], date(2022, 6, 9))

    meta = ingest.get_security(session, "META")
    assert meta.id == fb_id and meta.name == "Meta Platforms"
    assert ingest.get_security(session, "FB").id == fb_id  # old symbol still resolves
    history = session.execute(
        select(TickerHistory.ticker, TickerHistory.first_seen, TickerHistory.last_seen).order_by(
            TickerHistory.first_seen
        )
    ).all()
    assert history == [
        ("FB", date(2022, 6, 1), date(2022, 6, 1)),
        ("META", date(2022, 6, 9), date(2022, 6, 9)),
    ]
    assert session.get(Issuer, 1326801).name == "Meta Platforms"


def test_reused_ticker_gets_a_new_security(session):
    sync(session, [[1, "Old Co", "ABC", "NYSE"]], date(2020, 1, 1))
    old_id = ingest.get_security(session, "ABC").id
    sync(session, [[2, "New Co", "ABC", "Nasdaq"]], date(2024, 1, 1))

    new = ingest.get_security(session, "ABC")
    assert new.id != old_id and new.cik == 2
    old = session.get(Security, old_id)
    assert old.ticker is None and not old.active


def test_delisted_security_keeps_ticker_but_is_inactive(session):
    sync(session, [[1, "Gone Co", "GONE", "NYSE"], [2, "Stays", "STAY", "NYSE"]], date(2024, 1, 1))
    sync(session, [[2, "Stays", "STAY", "NYSE"]], date(2024, 2, 1))
    gone = ingest.get_security(session, "GONE")
    assert gone.ticker == "GONE" and not gone.active


def test_share_class_added_does_not_steal_existing_security(session):
    sync(session, [[1, "Co", "CO", "NYSE"]], date(2024, 1, 1))
    co_id = ingest.get_security(session, "CO").id
    sync(session, [[1, "Co", "CO", "NYSE"], [1, "Co", "CO-B", "NYSE"]], date(2024, 2, 1))
    assert ingest.get_security(session, "CO").id == co_id
    assert ingest.get_security(session, "CO-B").id != co_id


def test_relisting_reactivates(session):
    sync(session, [[1, "Co", "CO", "NYSE"]], date(2024, 1, 1))
    sync(session, [], date(2024, 2, 1))
    sync(session, [[1, "Co", "CO", "NYSE"]], date(2024, 3, 1))
    assert ingest.get_security(session, "CO").active
