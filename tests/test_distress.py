from datetime import date

from test_study_h001 import annual_facts, index

from fin_intel import distress, edgar, population, world
from fin_intel.models import CorporateEvent, DistressScore, Security
from fin_intel.studies import h001


def test_frozen_score_matches_the_model():
    row = h001.Row(1, date(2020, 3, 31), 1e8, signals=dict.fromkeys(h001.EVENT_SIGNALS, False))
    row.signals |= {"weak_z": False, "short_runway": False}
    quiet = h001.frozen_score(row)
    row.signals["late_filing"] = True
    assert h001.frozen_score(row) > quiet
    row.net_selling = 0.01  # heavy insider selling lowers the score
    assert h001.frozen_score(row) < h001.frozen_score(
        h001.Row(1, row.quarter, 1e8, signals=row.signals)
    )


def test_daily_alerts_on_watched_companies(session, monkeypatch):
    monkeypatch.setenv("FI_WATCHLIST", "SINK,FINE")
    from fin_intel.config import get_settings

    get_settings.cache_clear()
    population.load(
        session,
        index(
            [
                (1001, "10-K", "2015-03-01", "0001001-15-000001"),
                (1002, "10-K", "2015-03-01", "0001002-15-000001"),
            ]
        ),
    )
    session.commit()
    edgar.load_company_facts(
        session,
        1001,
        world.facts_payload(
            annual_facts("0001001-15-000001", 2014, "2015-03-01", 1000.0, 200e6, -500.0)
        ),
    )
    edgar.load_company_facts(
        session,
        1002,
        world.facts_payload(
            annual_facts("0001002-15-000001", 2014, "2015-03-01", 900.0, 500e6, 400.0, True)
        ),
    )
    session.add_all(
        [
            Security(ticker="SINK", origin="sec", cik=1001),
            Security(ticker="FINE", origin="sec", cik=1002),
            CorporateEvent(
                accession="a1", item="", cik=1001, form="NT 10-Q", filed=date(2015, 5, 15)
            ),
        ]
    )
    session.commit()

    # Before the late filing: the weak balance sheet already ranks SINK first of two.
    alerts = distress.sync(session, date(2015, 5, 1))
    assert [(a.tickers, a.new, a.signals) for a in alerts] == [
        (["SINK"], True, ["weak_z", "short_runway"])
    ]
    stored = {s.cik: s for s in session.query(DistressScore)}
    assert set(stored) == {1001, 1002} and not stored[1002].flagged
    again = distress.sync(session, date(2015, 6, 30))
    assert [a.new for a in again] == [False]  # still flagged: not a new alert
    assert "late_filing" in again[0].signals and again[0].score > alerts[0].score
    # Unflagged on its previous scoring, flagged now: new again.
    session.query(DistressScore).filter_by(day=date(2015, 6, 30)).update({"flagged": False})
    session.commit()
    assert [a.new for a in distress.sync(session, date(2015, 7, 1))] == [True]

    latest = distress.latest(session)
    assert [r["tickers"] for r in latest] == [["SINK"], ["FINE"]]
    assert latest[0]["flagged"] and not latest[1]["flagged"]
