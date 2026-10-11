from datetime import UTC, datetime, timedelta

import pytest
from conftest import TEST_DATABASE_URL
from typer.testing import CliRunner

from fin_intel import catalog
from fin_intel.db import get_engine
from fin_intel.models import SyncState


def state(provider, dataset, key, days_ago, error=None, failed_after=False):
    ok = datetime.now(UTC) - timedelta(days=days_ago)
    attempt = ok + timedelta(hours=1) if failed_after else ok
    return SyncState(
        provider=provider,
        dataset=dataset,
        key=key,
        last_attempt=attempt,
        last_success=ok,
        last_error=error,
    )


@pytest.fixture
def db_file(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FI_DATABASE_URL", TEST_DATABASE_URL)
    monkeypatch.setenv("FI_RAW_DIR", str(tmp_path / "raw"))
    get_engine.cache_clear()
    yield engine
    get_engine().dispose()
    get_engine.cache_clear()


def test_status(session, monkeypatch):
    monkeypatch.setattr(
        catalog,
        "EXPECTED",
        {("fred", "observations"): 4, ("cftc", "legacy"): 9, ("bea", "supply_use"): 35},
    )
    rows = [state("fred", "observations", f"S{i}", 1) for i in range(5)]
    rows += [state("fred", "observations", f"F{i}", 1, "HTTP 500", True) for i in range(3)]
    rows += [state("cftc", "legacy", "all", 12)]
    # Two stragglers out of many items don't make a feed failing.
    rows += [state("house", "ptr", f"d{i}", 2) for i in range(300)]
    rows += [state("house", "ptr", f"x{i}", 2, "scanned PDF", True) for i in range(2)]
    session.add_all(rows)
    session.commit()
    feeds = {f"{f.provider}/{f.dataset}": f for f in catalog.feeds(session)}
    assert feeds["fred/observations"].status == "failing"
    assert feeds["fred/observations"].failing == 3
    assert feeds["fred/observations"].last_error == "HTTP 500"
    assert feeds["cftc/legacy"].status == "stale"
    assert feeds["bea/supply_use"].status == "stale"  # monitored, never synced
    assert feeds["house/ptr"].status == "unmonitored"
    assert "io_uses" in feeds["bea/supply_use"].tables
    assert {f.dataset for f in catalog.alerts(session)} == {"observations", "legacy", "supply_use"}
    assert catalog.table_rows(session)["sync_state"] >= 0


def test_cli_check_exits_on_alerts(session, monkeypatch, db_file):
    from fin_intel.cli import app

    monkeypatch.setattr(catalog, "EXPECTED", {("cftc", "legacy"): 9})
    session.add(state("cftc", "legacy", "all", 1))
    session.commit()
    runner = CliRunner()
    ok = runner.invoke(app, ["catalog", "--check"])
    assert ok.exit_code == 0 and "ok" in ok.output
    monkeypatch.setattr(catalog, "EXPECTED", {("cftc", "legacy"): 9, ("bea", "supply_use"): 35})
    alert = runner.invoke(app, ["catalog", "--check"])
    assert alert.exit_code == 1 and "bea/supply_use" in alert.output
