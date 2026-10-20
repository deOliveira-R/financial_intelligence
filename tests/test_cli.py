"""CLI runs against the test database through its own engine and connections."""

import copy

import pytest
import respx
from conftest import COMPANY_FACTS, TEST_DATABASE_URL
from sqlalchemy import select
from typer.testing import CliRunner

from fin_intel.cli import app
from fin_intel.db import get_engine
from fin_intel.models import RawResponse, SyncRun

TICKERS = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [[320193, "Apple Inc.", "AAPL", "Nasdaq"], [789019, "Microsoft", "MSFT", "Nasdaq"]],
}


@pytest.fixture
def db_file(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FI_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("FI_RAW_DIR", str(tmp_path / "raw"))
    get_engine.cache_clear()
    yield engine
    get_engine().dispose()
    get_engine.cache_clear()


@respx.mock
def test_multi_item_sync_on_file_database(db_file):
    msft = copy.deepcopy(COMPANY_FACTS)
    msft["cik"], msft["entityName"] = 789019, "Microsoft"
    for unit_facts in msft["facts"]["us-gaap"].values():
        for facts in unit_facts["units"].values():
            for f in facts:
                f["accn"] = "M" + f["accn"]
    respx.get("https://www.sec.gov/files/company_tickers_exchange.json").respond(json=TICKERS)
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").respond(
        json=COMPANY_FACTS
    )
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json").respond(json=msft)

    runner = CliRunner()
    assert runner.invoke(app, ["sync-tickers"]).exit_code == 0
    result = runner.invoke(app, ["sync-fundamentals", "AAPL", "MSFT"])
    assert result.exit_code == 0, result.output

    with db_file.connect() as conn:
        runs = conn.execute(select(SyncRun.job, SyncRun.status, SyncRun.items_ok)).all()
        assert runs == [("sync-tickers", "ok", 1), ("sync-fundamentals", "ok", 2)]
        assert len(conn.execute(select(RawResponse.id)).all()) == 3

    rebuilt = runner.invoke(app, ["rebuild", "fundamentals"])
    assert rebuilt.exit_code == 0 and "replayed     2 sec/companyfacts" in rebuilt.output


@respx.mock
def test_failed_item_marks_run_partial(db_file):
    respx.get("https://www.sec.gov/files/company_tickers_exchange.json").respond(json=TICKERS)
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json").respond(
        json=COMPANY_FACTS
    )
    respx.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000789019.json").respond(404)
    runner = CliRunner()
    runner.invoke(app, ["sync-tickers"])
    result = runner.invoke(app, ["sync-fundamentals", "AAPL", "MSFT"])
    assert result.exit_code == 1
    with db_file.connect() as conn:
        run = conn.execute(select(SyncRun).where(SyncRun.job == "sync-fundamentals")).one()
        assert (run.status, run.items_ok, run.items_failed) == ("partial", 1, 1)
        assert run.message == "failed: MSFT"


def test_scheduled_syncs_run_every_step_and_report_failures(db_file, monkeypatch):
    import typer

    from fin_intel import fx
    from fin_intel.cli import schedule
    from fin_intel.models import JobState
    from fin_intel.providers import ProviderError

    calls = []

    def ok(name):
        return lambda *a, **k: calls.append(name)

    def failing(*a, **k):
        calls.append("market")
        raise ProviderError("massive: HTTP 503: unavailable", 503)

    monkeypatch.setattr(schedule, "execute", schedule.run_step)  # in this process
    monkeypatch.setenv("FI_WATCHLIST", "aapl, msft")
    monkeypatch.setenv("FI_FRED_SERIES", "GDP")
    monkeypatch.setattr(schedule, "sync_market_daily", failing)
    monkeypatch.setattr(schedule, "sync_actions", ok("actions"))
    monkeypatch.setattr(schedule, "sync_economic", lambda series: calls.append(("fred", series)))
    monkeypatch.setattr(schedule, "sync_prices", lambda tickers: calls.append(("prices", tickers)))
    monkeypatch.setattr(schedule, "derive_breadth_cmd", ok("breadth"))
    monkeypatch.setattr(schedule, "sync_fundamentals_bulk", ok("fundamentals"))
    monkeypatch.setattr(schedule, "derive_metrics_cmd", ok("metrics"))
    monkeypatch.setattr(schedule, "sync_insiders", ok("insiders"))
    monkeypatch.setattr(schedule, "sync_congress", ok("congress"))
    monkeypatch.setattr(schedule, "sync_eia", ok("eia"))
    monkeypatch.setattr(schedule, "sync_japan", ok("japan"))
    monkeypatch.setattr(schedule, "sync_cboe", ok("cboe"))
    monkeypatch.setattr(schedule, "sync_dart", ok("dart"))
    monkeypatch.setattr(schedule, "sync_edinet", ok("edinet"))
    monkeypatch.setattr(schedule, "sync_tw_prices", ok("tw prices"))
    monkeypatch.setattr(schedule, "distress_cmd", ok("distress"))

    def exit_1():
        calls.append("insiders")
        raise typer.Exit(1)  # failed items, no provider error recorded

    monkeypatch.setattr(schedule, "sync_insiders", exit_1)

    result = CliRunner().invoke(app, ["sync-daily"])
    # A failed step doesn't stop the others, but the command still fails.
    assert result.exit_code == 1, result.output
    assert "market-bars: retrying" in result.output and "metrics: ok" in result.output
    assert calls == [
        "market",
        "actions",
        "breadth",  # its input failed and retries later: it doesn't wait (runs again then)
        "fundamentals",
        "insiders",
        "congress",
        ("fred", ["GDP", *(series for series, _ in fx.SERIES.values())]),  # plus FX rates
        "eia",
        "japan",
        "cboe",
        "dart",
        "edinet",
        "tw prices",
        "metrics",
        "distress",
        ("prices", ["AAPL", "MSFT"]),
    ]
    with db_file.connect() as conn:
        states = dict(conn.execute(select(JobState.name, JobState.error_kind)).all())
    assert states["market-bars"] == "transient" and states["insiders"] == "error"
    status = CliRunner().invoke(app, ["jobs", "status"])
    assert "retrying   market-bars" in status.output and "HTTP 503" in status.output


@respx.mock
def test_sync_news_stalest_refreshes_the_queries_left_longest(db_file, monkeypatch):
    from datetime import UTC, date, datetime, timedelta

    from fin_intel import news
    from fin_intel.models import SyncState

    monkeypatch.setattr(news, "FIRST_YEAR", date.today().year)
    monkeypatch.setattr(news, "QUERIES", {n: (n, f'"{n}"') for n in ("fresh", "never", "older")})
    now = datetime.now(UTC)
    with db_file.begin() as conn:
        conn.execute(
            SyncState.__table__.insert(),
            [
                {
                    "provider": "gdelt",
                    "dataset": "doc_timeline",
                    "key": k,
                    "last_attempt": t,
                    "last_success": t,
                }
                for k, t in (("fresh", now - timedelta(days=1)), ("older", now - timedelta(days=5)))
            ],
        )
    route = respx.get("https://api.gdeltproject.org/api/v2/doc/doc").respond(json={})
    result = CliRunner().invoke(app, ["sync-news", "--stalest", "2"])
    assert result.exit_code == 0, result.output
    assert [c.request.url.params["query"] for c in route.calls] == ['"never"', '"older"']


def test_steps_run_in_their_own_process_and_report_how_they_ended(db_file):
    from datetime import timedelta

    from fin_intel import jobs
    from fin_intel.cli import schedule

    # A step the child doesn't know fails before it can report: a crash, with its output.
    outcome = schedule.execute(jobs.Step("no-such-step", "x", "daily", lambda: None))
    assert (outcome.ok, outcome.kind) == (False, "crash")
    assert "unknown step" in outcome.output
    names = [s.name for s in schedule.steps()]
    assert len(names) == len(set(names))
    known = set(names)
    assert all(set(s.after) <= known for s in schedule.steps())
    assert all(s.timeout >= timedelta(hours=1) for s in schedule.steps())
