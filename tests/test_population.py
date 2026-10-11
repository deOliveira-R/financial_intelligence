from datetime import date

import respx
from sqlalchemy import select

from fin_intel import pipeline, population
from fin_intel.models import Issuer, Security
from fin_intel.providers import SecProvider


def index(*rows):
    head = "Description: XBRL Index\n\nCIK|Company Name|Form Type|Date Filed|Filename\n" + "-" * 80
    lines = [f"{c}|{n}|{f}|{d}|edgar/data/{c}/{a}.txt" for c, n, f, d, a in rows]
    return ("\n".join([head, *lines]) + "\n").encode("latin-1")


Q1 = index(
    (1001, "LEHMAN BROTHERS HOLDINGS INC", "10-K", "2012-02-10", "0001001-12-000001"),
    (1001, "LEHMAN BROTHERS HOLDINGS INC", "10-Q", "2012-03-20", "0001001-12-000002"),
    (2002, "SOME FUND TRUST", "485BPOS", "2012-03-01", "0002002-12-000001"),
    (320193, "APPLE INC", "10-Q", "2012-01-25", "0000320193-12-000001"),
)


def test_members_are_periodic_filers_dead_or_alive(session):
    session.add(Issuer(cik=320193, name="Apple Inc."))
    session.flush()
    assert population.load(session, Q1) == 4
    session.commit()
    # Periodic filers get an issuer row (named as filed); funds' XBRL filings don't count.
    assert session.get(Issuer, 1001).name == "LEHMAN BROTHERS HOLDINGS INC"
    assert session.get(Issuer, 2002) is None
    assert session.get(Issuer, 320193).name == "Apple Inc."  # known issuers keep their name
    assert population.members(session) == {1001, 320193}
    assert population.members(session, since=date(2012, 2, 1)) == {1001}
    # The fundamentals sync follows them even without a listed security.
    session.add(Security(ticker="AAPL", security_type="CS", origin="sec", cik=320193))
    session.commit()
    assert pipeline.tracked_ciks(session) == {1001, 320193}


@respx.mock
def test_sync_fetches_each_past_quarter_once(session, raw_store, monkeypatch):
    monkeypatch.setattr(population, "utc_today", lambda: date(2009, 8, 15))
    base = "https://www.sec.gov/Archives/edgar/full-index/2009"
    routes = {q: respx.get(f"{base}/QTR{q}/xbrl.idx").respond(content=index()) for q in (1, 2, 3)}
    sec = SecProvider(raw_store=raw_store)
    population.sync_population(session, sec)
    population.sync_population(session, sec)
    # Q1 once; the previous and current quarters (Q2, Q3) on every run.
    assert [routes[q].call_count for q in (1, 2, 3)] == [1, 2, 2]
    assert session.scalars(select(Issuer)).all() == []
