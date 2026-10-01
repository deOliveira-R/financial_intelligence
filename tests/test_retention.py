from datetime import UTC, datetime, timedelta

from fin_intel.ingest import SNAPSHOT_DATASETS
from fin_intel.raw import prune

OLD = datetime.now(UTC) - timedelta(days=60)
SNAPSHOT = ("sec", "companyfacts")


def save(store, dataset, key, body, status=200, when=OLD, provider="sec"):
    return store.save(provider, dataset, key, None, status, body, fetched_at=when)


def kept_ids(store):
    return [r.id for r in store.records()]


def test_keeps_latest_snapshots_per_key(raw_store, tmp_path):
    ids = [save(raw_store, "companyfacts", "1", f"v{i}".encode()) for i in range(5)]
    other = save(raw_store, "companyfacts", "2", b"other")
    result = prune(raw_store, SNAPSHOT_DATASETS, keep=2)
    assert result.responses == 3 and result.files == 3
    assert kept_ids(raw_store) == [ids[3], ids[4], other]
    assert len(list((tmp_path / "raw").rglob("*.gz"))) == 3


def test_never_prunes_incremental_datasets_or_recent_responses(raw_store):
    for i in range(5):
        save(raw_store, "daily_prices", "AAPL", f"{i}".encode(), provider="tiingo")
        save(raw_store, "companyfacts", "1", f"recent{i}".encode(), when=datetime.now(UTC))
    assert prune(raw_store, SNAPSHOT_DATASETS, keep=1).responses == 0


def test_drops_old_errors_but_not_recent_ones(raw_store):
    save(raw_store, "companyfacts", "1", b"boom", status=500)
    save(raw_store, "companyfacts", "1", b"boom2", status=500, when=datetime.now(UTC))
    assert prune(raw_store, SNAPSHOT_DATASETS).responses == 1


def test_shared_bodies_survive_while_referenced(raw_store, tmp_path):
    # The same body fetched three times: pruning two index rows must keep the file.
    for _ in range(3):
        save(raw_store, "companyfacts", "1", b"same")
    result = prune(raw_store, SNAPSHOT_DATASETS, keep=1)
    assert (result.responses, result.files) == (2, 0)
    assert len(list((tmp_path / "raw").rglob("*.gz"))) == 1
    (record,) = raw_store.records()
    assert record.body == b"same"


def test_dry_run_changes_nothing(raw_store, tmp_path):
    for i in range(3):
        save(raw_store, "companyfacts", "1", f"v{i}".encode())
    result = prune(raw_store, SNAPSHOT_DATASETS, keep=1, dry_run=True)
    assert (result.responses, result.files) == (2, 2)
    assert len(list(raw_store.records())) == 3
    assert len(list((tmp_path / "raw").rglob("*.gz"))) == 3
