from collections.abc import Iterable, Iterator, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import BigInteger, Connection, create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fin_intel.config import get_settings


class Base(DeclarativeBase):
    # 64-bit integers everywhere: issuer ids from other regulators start at 10^10, and some
    # OTC volumes exceed 2^31 (SQLite never noticed; PostgreSQL's INTEGER is 32-bit).
    type_annotation_map = {int: BigInteger}


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(connection: Connection | None = None) -> AlembicConfig:
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["connection"] = connection
    return config


def init_db(engine: Engine | None = None) -> None:
    """Bring the database schema up to date by running pending migrations."""
    engine = engine or get_engine()
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "head")


def session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    return sessionmaker(engine or get_engine(), expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with session_factory()() as session:
        yield session


def upsert(
    session: Session,
    model: type[Base],
    rows: Iterable[dict[str, Any]],
    key: Sequence[str],
    update: Sequence[str] | None = None,
    chunk_size: int = 5000,
) -> int:
    """Insert rows, updating non-key columns (or just `update`) when the key already exists.

    One parameterized statement, compiled once and executed per chunk of rows (executemany):
    building a multi-row VALUES statement per chunk spent most of a load compiling SQL.
    Rows repeating a key within the batch are collapsed (last wins): PostgreSQL rejects an
    ON CONFLICT DO UPDATE that touches the same row twice.
    """
    rows = list({tuple(r[k] for k in key): r for r in rows}.values())
    if not rows:
        return 0
    stmt = postgresql.insert(model.__table__)
    targets = rows[0].keys() if update is None else update  # [] means insert-only
    updates = {c: stmt.excluded[c] for c in targets if c not in key}
    if updates:
        stmt = stmt.on_conflict_do_update(index_elements=list(key), set_=updates)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=list(key))
    connection = session.connection()
    for i in range(0, len(rows), chunk_size):
        connection.execute(stmt, rows[i : i + chunk_size])
    return len(rows)
