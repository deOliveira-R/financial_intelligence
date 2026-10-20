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
    from fin_intel.cli import ops

    calls = []

    def ok(name):
        return lambda *a, **k: calls.append(name)

    def failing(*a, **k):
        calls.append("market")
        raise typer.Exit(1)

    monkeypatch.setenv("FI_WATCHLIST", "aapl, msft")
    monkeypatch.setenv("FI_FRED_SERIES", "GDP")
    monkeypatch.setattr(ops, "sync_market_daily", failing)
    monkeypatch.setattr(ops, "sync_actions", ok("actions"))
    monkeypatch.setattr(ops, "sync_economic", lambda series: calls.append(("fred", series)))
    monkeypatch.setattr(ops, "sync_prices", lambda tickers: calls.append(("prices", tickers)))
    monkeypatch.setattr(ops, "derive_breadth_cmd", ok("breadth"))
    monkeypatch.setattr(ops, "sync_fundamentals_bulk", ok("fundamentals"))
    monkeypatch.setattr(ops, "derive_metrics_cmd", ok("metrics"))
    monkeypatch.setattr(ops, "sync_insiders", ok("insiders"))
    monkeypatch.setattr(ops, "sync_congress", ok("congress"))
    monkeypatch.setattr(ops, "sync_eia", ok("eia"))
    monkeypatch.setattr(ops, "sync_japan", ok("japan"))
    monkeypatch.setattr(ops, "sync_cboe", ok("cboe"))
    monkeypatch.setattr(ops, "sync_dart", ok("dart"))
    monkeypatch.setattr(ops, "sync_edinet", ok("edinet"))
    monkeypatch.setattr(ops, "sync_tw_prices", ok("tw prices"))

    result = CliRunner().invoke(app, ["sync-daily"])
    # A failed step doesn't stop the others, but the command still fails.
    assert result.exit_code == 1 and "failed steps: market bars" in result.output
    assert calls == [
        "market",
        "actions",
        "breadth",
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
        ("prices", ["AAPL", "MSFT"]),
    ]


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
