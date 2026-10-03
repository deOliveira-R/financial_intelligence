"""Raw layer: every provider response, kept so tables can be rebuilt without the network.

Bodies are stored once per content hash under raw_dir/<provider>/<hash[:2]>/<hash>.gz;
`raw_responses` indexes each call (provider, dataset, key, params, time, status).
"""

import gzip
import hashlib
import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, insert, select
from sqlalchemy.engine import Connection, Engine

from fin_intel.models import RawResponse


@dataclass(frozen=True)
class RawRecord:
    id: int
    provider: str
    dataset: str
    key: str | None
    params: dict[str, Any]
    fetched_at: datetime
    status: int
    path: Path

    @property
    def body(self) -> bytes:
        """Read and decompressed on access: a full rebuild iterates tens of thousands of
        responses (company facts alone are ~10 GB decompressed), so bodies aren't held."""
        return gzip.decompress(self.path.read_bytes())

    def json(self) -> Any:
        return json.loads(self.body)


class RawStore:
    def __init__(self, engine: Engine, root: str | Path):
        self.engine = engine
        self.root = Path(root)

    def _path(self, provider: str, content_hash: str) -> Path:
        return self.root / provider / content_hash[:2] / f"{content_hash}.gz"

    def save(
        self,
        provider: str,
        dataset: str,
        key: str | None,
        params: dict[str, Any] | None,
        status: int,
        body: bytes,
        fetched_at: datetime | None = None,
    ) -> int:
        content_hash = hashlib.sha256(body).hexdigest()
        path = self._path(provider, content_hash)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(body))
            tmp.replace(path)  # atomic: a crash never leaves a truncated body behind
        # Own transaction: the record survives even if the caller's load later fails.
        with self.engine.begin() as conn:
            return conn.execute(
                insert(RawResponse).returning(RawResponse.id),
                {
                    "provider": provider,
                    "dataset": dataset,
                    "key": key,
                    "params": json.dumps(params, sort_keys=True, default=str) if params else None,
                    "fetched_at": fetched_at or datetime.now(UTC),
                    "status": status,
                    "content_hash": content_hash,
                    "size": len(body),
                },
            ).scalar_one()

    def records(
        self,
        provider: str | None = None,
        datasets: list[str] | None = None,
        latest_per_key: bool = False,
        connection: Connection | None = None,
    ) -> Iterator[RawRecord]:
        """Successful responses in fetch order, optionally only the latest per (dataset, key).

        Pass `connection` to read inside a caller's open transaction (e.g. a session's);
        a separate connection could block on, or in tests reset, the caller's writes.
        """
        stmt = select(RawResponse).where(RawResponse.status == 200)
        if provider:
            stmt = stmt.where(RawResponse.provider == provider)
        if datasets:
            stmt = stmt.where(RawResponse.dataset.in_(datasets))
        if latest_per_key:
            latest = (
                select(func.max(RawResponse.id))
                .where(RawResponse.status == 200)
                .group_by(RawResponse.provider, RawResponse.dataset, RawResponse.key)
            )
            stmt = stmt.where(RawResponse.id.in_(latest))
        stmt = stmt.order_by(RawResponse.fetched_at, RawResponse.id)
        if connection is not None:
            rows = connection.execute(stmt).all()
        else:
            with self.engine.connect() as conn:
                rows = conn.execute(stmt).all()
        for r in rows:
            yield RawRecord(
                id=r.id,
                provider=r.provider,
                dataset=r.dataset,
                key=r.key,
                params=json.loads(r.params) if r.params else {},
                fetched_at=_aware(r.fetched_at),
                status=r.status,
                path=self._path(r.provider, r.content_hash),
            )

    def latest_hashes(self, provider: str, dataset: str) -> dict[str | None, str]:
        """Content hash of the latest successful response per key, e.g. to skip companies
        whose bulk-file entry hasn't changed since it was last loaded."""
        latest = (
            select(RawResponse.key, func.max(RawResponse.id).label("id"))
            .where(
                RawResponse.provider == provider,
                RawResponse.dataset == dataset,
                RawResponse.status == 200,
            )
            .group_by(RawResponse.key)
            .subquery()
        )
        with self.engine.connect() as conn:
            rows = conn.execute(
                select(RawResponse.key, RawResponse.content_hash).join(
                    latest, latest.c.id == RawResponse.id
                )
            )
            return dict(rows.all())

    def call_times(self, provider: str, since: datetime) -> list[datetime]:
        """When this provider was called (any status), for rate limits across processes."""
        with self.engine.connect() as conn:
            rows = conn.scalars(
                select(RawResponse.fetched_at).where(
                    RawResponse.provider == provider, RawResponse.fetched_at >= since
                )
            ).all()
        return [_aware(t) for t in rows]

    def distinct_keys(self, provider: str, datasets: list[str], since: datetime) -> set[str]:
        with self.engine.connect() as conn:
            return set(
                conn.scalars(
                    select(RawResponse.key)
                    .distinct()
                    .where(
                        RawResponse.provider == provider,
                        RawResponse.dataset.in_(datasets),
                        RawResponse.fetched_at >= since,
                        RawResponse.key.is_not(None),
                    )
                )
            )


@dataclass(frozen=True)
class PruneResult:
    responses: int
    files: int
    bytes: int


def prune(
    store: RawStore,
    snapshot_datasets: set[tuple[str, str]],
    keep: int = 3,
    min_age: timedelta = timedelta(days=31),
    dry_run: bool = False,
) -> PruneResult:
    """Drop raw responses a rebuild can't need, then delete bodies nothing references.

    - Snapshot datasets (each response complete): keep the latest `keep` per key.
    - Error responses: never replayed, so dropped.
    - Everything else (incremental datasets, ticker lists) is kept: rebuilds replay all of it.
    Nothing younger than `min_age` is touched: rate limits and Tiingo's monthly symbol cap
    count recent calls.
    """
    cutoff = datetime.now(UTC) - min_age
    with store.engine.connect() as conn:
        rows = conn.execute(
            select(
                RawResponse.id,
                RawResponse.provider,
                RawResponse.dataset,
                RawResponse.key,
                RawResponse.status,
                RawResponse.fetched_at,
                RawResponse.content_hash,
            ).order_by(RawResponse.id.desc())
        ).all()

    doomed: set[int] = set()
    seen: Counter[tuple] = Counter()
    for r in rows:  # newest first
        old = _aware(r.fetched_at) < cutoff
        if r.status != 200:
            if old:
                doomed.add(r.id)
            continue
        if (r.provider, r.dataset) in snapshot_datasets:
            seen[(r.provider, r.dataset, r.key)] += 1
            if seen[(r.provider, r.dataset, r.key)] > keep and old:
                doomed.add(r.id)

    # Bodies are shared by identical responses; delete only those no survivor references.
    surviving = {(r.provider, r.content_hash) for r in rows if r.id not in doomed}
    orphans = {(r.provider, r.content_hash) for r in rows if r.id in doomed} - surviving
    paths = [store._path(p, h) for p, h in orphans]
    size = sum(path.stat().st_size for path in paths if path.exists())
    if not dry_run:
        with store.engine.begin() as conn:
            for chunk in _chunks(sorted(doomed), 500):
                conn.execute(delete(RawResponse).where(RawResponse.id.in_(chunk)))
        for path in paths:  # after the commit: a crash can leave a stray file, never a gap
            path.unlink(missing_ok=True)
    return PruneResult(responses=len(doomed), files=len(paths), bytes=size)


def _chunks(items: list, n: int):
    for i in range(0, len(items), n):
        yield items[i : i + n]


def _aware(t: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def default_store() -> RawStore:
    from fin_intel.config import get_settings
    from fin_intel.db import get_engine

    return RawStore(get_engine(), get_settings().raw_dir)
