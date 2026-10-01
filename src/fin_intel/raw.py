"""Raw layer: every provider response, kept so tables can be rebuilt without the network.

Bodies are stored once per content hash under raw_dir/<provider>/<hash[:2]>/<hash>.gz;
`raw_responses` indexes each call (provider, dataset, key, params, time, status).
"""

import gzip
import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.engine import Engine

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
    body: bytes

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
    ) -> Iterator[RawRecord]:
        """Successful responses in fetch order, optionally only the latest per (dataset, key)."""
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
        with self.engine.connect() as conn:
            rows = conn.execute(stmt.order_by(RawResponse.fetched_at, RawResponse.id)).all()
        for r in rows:
            yield RawRecord(
                id=r.id,
                provider=r.provider,
                dataset=r.dataset,
                key=r.key,
                params=json.loads(r.params) if r.params else {},
                fetched_at=_aware(r.fetched_at),
                status=r.status,
                body=gzip.decompress(self._path(r.provider, r.content_hash).read_bytes()),
            )

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


def _aware(t: datetime) -> datetime:
    # SQLite returns naive datetimes; everything is stored in UTC.
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def default_store() -> RawStore:
    from fin_intel.config import get_settings
    from fin_intel.db import get_engine

    return RawStore(get_engine(), get_settings().raw_dir)
