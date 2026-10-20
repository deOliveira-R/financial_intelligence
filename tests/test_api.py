from datetime import UTC, date, datetime

import pytest
from conftest import COMPANY_FACTS, FRED_OBS, FRED_SERIES, TICKERS, tiingo_bar
from fastapi.testclient import TestClient

from fin_intel import edgar, macro, market
from fin_intel.api import app
from fin_intel.db import get_session, session_factory
from fin_intel.models import SyncRun


@pytest.fixture
def client(engine, session):
    market.load_company_tickers(session, TICKERS, date(2026, 1, 1))
    edgar.load_company_facts(session, 320193, COMPANY_FACTS)
    # AAPL 2-for-1 split on 2026-09-02 and a $1 dividend on 2026-09-03.
    market.load_tiingo_daily(
        session,
        "AAPL",
        [
            tiingo_bar("2026-09-01", 200),
            tiingo_bar("2026-09-02", 100, split=2.0),
            tiingo_bar("2026-09-03", 101, div=1.0),
        ],
    )
    macro.load_fred_series(session, FRED_SERIES)
    macro.load_fred_observations(session, "UNRATE", FRED_OBS)
    session.add(SyncRun(job="sync-prices", started_at=datetime.now(UTC), status="ok"))
    session.commit()

    def override():
        with session_factory(engine)() as s:
            yield s

    app.dependency_overrides[get_session] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_securities(client):
    assert client.get("/securities", params={"q": "appl"}).json()[0]["ticker"] == "AAPL"
    detail = client.get("/securities/aapl").json()
    assert detail["cik"] == 320193
    assert detail["ticker_history"] == [
        {"ticker": "AAPL", "first_seen": "2026-01-01", "last_seen": "2026-01-01"}
    ]
    assert client.get("/securities/NOPE").status_code == 404


def test_prices_are_adjusted_on_read(client):
    bars = client.get("/prices/AAPL/daily").json()
    assert [b["close"] for b in bars] == [200, 100, 101]
    first = bars[0]
    assert first["adj_close"] == pytest.approx(200 / 2 * (1 - 1 / 100))
    assert first["adj_volume"] == 2000
    # A range still adjusts for actions after its end.
    (only,) = client.get("/prices/AAPL/daily", params={"end": "2026-09-01"}).json()
    assert only["adj_close"] == first["adj_close"]
    actions = client.get("/prices/AAPL/actions").json()
    assert [(a["action"], a["value"]) for a in actions] == [("split", 2.0), ("dividend", 1.0)]


def test_fundamentals_latest_vs_as_reported(client):
    params = {"concept": "Revenues", "period_type": "annual"}
    latest = client.get("/fundamentals/AAPL", params=params).json()
    assert [(f["fiscal_year"], f["value"]) for f in latest] == [(2024, 101.0), (2025, 120.0)]
    reported = client.get("/fundamentals/AAPL", params={**params, "as_reported": True}).json()
    assert [f["value"] for f in reported] == [100.0, 101.0, 120.0]


def test_fundamentals_q4_derived_and_split_adjusted(client):
    quarters = client.get(
        "/fundamentals/AAPL", params={"concept": "Revenues", "period_type": "quarter"}
    ).json()
    assert [(f["fiscal_period"], f["value"], f["derived"]) for f in quarters] == [
        ("Q4", 30.0, True)
    ]
    # EPS filed in 2024, before the 2026 2-for-1 split.
    (eps,) = client.get("/fundamentals/AAPL", params={"concept": "EarningsPerShareDiluted"}).json()
    assert (eps["unit"], eps["value"], eps["split_adjustment"]) == ("USD/shares", 4.0, 2.0)


def test_fundamentals_metadata(client):
    concepts = client.get("/fundamentals/AAPL/concepts").json()
    revenues = next(c for c in concepts if c["concept"] == "Revenues")
    assert (revenues["label"], revenues["unit"], revenues["facts"]) == ("Revenues", "USD", 4)
    filings = client.get("/fundamentals/AAPL/filings", params={"form": "10-K"}).json()
    assert [f["accession"] for f in filings] == ["A-2025", "A-2024"]
    assert filings[0]["report_period_end"] == "2025-09-27"
    (calendar,) = client.get("/fundamentals/AAPL/calendar").json()
    assert (calendar["year_end_month"], calendar["year_end_day"]) == (9, 27)


def test_economic(client):
    assert client.get("/economic/unrate").json()["title"] == "Unemployment Rate"
    assert client.get("/economic/UNRATE/observations").json() == [
        {"date": "2026-07-01", "value": 4.2},
        {"date": "2026-08-01", "value": None},
    ]


def test_status(client, session):
    from datetime import UTC, datetime

    from fin_intel import jobs
    from fin_intel.cli.schedule import steps

    status = client.get("/status").json()
    assert status["recent_runs"][0]["job"] == "sync-prices"
    assert status["failing"] == []
    assert {j["status"] for j in status["jobs"]} == {"never run"}

    now = datetime.now(UTC)
    bars = next(s for s in steps() if s.name == "market-bars")
    jobs.start(session, bars, now)
    outcome = jobs.Outcome(ok=False, kind="auth", error="HTTP 401", output="traceback...")
    jobs.finish(session, steps(), bars, outcome, now, now)
    job = next(j for j in client.get("/status").json()["jobs"] if j["name"] == "market-bars")
    assert (job["status"], job["error_kind"], job["label"]) == ("retrying", "auth", "market bars")
    assert "check the key" in job["hint"]
    events = client.get("/jobs/market-bars/events").json()
    assert [(e["status"], e["output"]) for e in events] == [("retrying", "traceback...")]


def test_api_key_required_when_configured(client, monkeypatch):
    from fin_intel.config import get_settings

    assert client.get("/securities/AAPL").status_code == 200  # no key configured: open
    monkeypatch.setenv("FI_API_KEY", "s3cret")
    get_settings.cache_clear()
    assert client.get("/securities/AAPL").status_code == 401
    assert client.get("/securities/AAPL", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/securities/AAPL", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200  # uptime checks stay open
    # The dashboard: a tailnet user named by Tailscale Serve, when allowed.
    tailnet = {"Tailscale-User-Login": "me@example.com"}
    assert client.get("/securities/AAPL", headers=tailnet).status_code == 200
    monkeypatch.setenv("FI_TAILNET_USERS", "someone@example.com, ME@example.com")
    get_settings.cache_clear()
    assert client.get("/securities/AAPL", headers=tailnet).status_code == 200
    other = {"Tailscale-User-Login": "other@example.com"}
    assert client.get("/securities/AAPL", headers=other).status_code == 401
    monkeypatch.setenv("FI_TAILNET_USERS", "")
    get_settings.cache_clear()
    assert client.get("/securities/AAPL", headers=tailnet).status_code == 401


def test_settings_saved_from_the_dashboard_apply_over_the_environment(client, session):
    from fin_intel import jobs
    from fin_intel.cli.schedule import steps
    from fin_intel.config import get_settings
    from fin_intel.models import SyncState

    def setting(name):
        return next(s for s in client.get("/settings").json() if s["name"] == name)

    fred = setting("fred_api_key")
    assert (fred["source"], fred["value"], fred["env"]) == ("env", "••••••••", "FI_FRED_API_KEY")
    assert (setting("lda_api_key")["status"], setting("eia_api_key")["source"]) == (
        "not set",
        "default",
    )
    # A key the provider refused.
    now = datetime.now(UTC)
    session.add(
        SyncState(
            provider="fred",
            dataset="series",
            key="UNRATE",
            last_attempt=now,
            last_error="HTTP 401 Unauthorized",
        )
    )
    dart = next(s for s in steps() if s.name == "dart")
    jobs.start(session, dart, now)
    blocked = jobs.Outcome(ok=False, kind="not_configured", error="set FI_OPENDART_API_KEY")
    jobs.finish(session, steps(), dart, blocked, now, now)
    assert setting("fred_api_key")["status"] == "rejected"
    assert setting("opendart_api_key")["status"] == "missing"

    # Saving a new key: masked, applied, and the blocked step retried now.
    saved = client.put("/settings/opendart_api_key", json={"value": " new-dart-key-1234 "})
    assert saved.json() == {"retried": ["dart"]}
    dart_key = setting("opendart_api_key")
    assert (dart_key["source"], dart_key["value"]) == ("dashboard", "••••••••1234")
    assert get_settings().opendart_api_key == "new-dart-key-1234"
    session.expire_all()  # still blocked until it runs, but runnable now
    assert jobs.next_runnable(session, steps(), datetime.now(UTC)).name == "dart"

    client.put("/settings/watchlist", json={"value": "aapl, msft\nasml"})
    assert get_settings().watchlist_tickers == ["AAPL", "MSFT", "ASML"]
    assert client.delete("/settings/opendart_api_key").status_code == 204
    assert get_settings().opendart_api_key == "dart-key"
    assert client.put("/settings/database_url", json={"value": "x"}).status_code == 404
    assert client.put("/settings/fred_api_key", json={"value": " "}).status_code == 400


def test_dashboard_is_served_at_the_root(client, monkeypatch, tmp_path):
    import fin_intel.api as api

    monkeypatch.setattr(api, "UI_DIR", tmp_path)
    assert client.get("/").status_code == 404
    (tmp_path / "index.html").write_text("<!doctype html><title>fin-intel</title>")
    page = client.get("/")
    assert (page.status_code, page.headers["cache-control"]) == (200, "no-cache")
    assert "fin-intel" in page.text


def test_congress_endpoints(client, session):
    from pathlib import Path

    from fin_intel import congress

    pdf = (Path(__file__).parent / "fixtures" / "20034836.pdf").read_bytes()
    congress.load_report(session, *congress.parse_house_ptr("20034836", pdf))
    session.commit()
    trades = client.get("/congress/trades", params={"member": "pelosi"}).json()
    assert [t["ticker"] for t in trades] == ["INTC", "UBER"]
    assert trades[0]["amount_min"] == 1_000_001 and trades[0]["filed"] == "2026-06-23"
    assert client.get("/congress/trades", params={"ticker": "AAPL"}).json() == []
    popular = client.get("/congress/popular", params={"days": 730}).json()
    assert {p["ticker"] for p in popular} == {"INTC", "UBER"}


def test_backtest_endpoint(client, session):
    from test_timeseries import add_bars

    add_bars(session, "SPY", date(2026, 1, 1), [100.0, 110.0, 99.0, 120.0, 120.0])
    r = client.get("/backtest", params={"asset": "SPY", "rule": "px:SPY > 105", "curve": True})
    assert r.status_code == 200
    body = r.json()
    assert body["strategy"]["total_return"] < 0 and len(body["curve"]) == 4
    assert body["trade_list"][0]["entry"] == "2026-01-02"
    bad = client.get("/backtest", params={"asset": "SPY", "rule": "px:SPY"})
    assert bad.status_code == 400
