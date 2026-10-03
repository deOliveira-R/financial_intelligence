from collections.abc import Iterable, Iterator, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import Connection, create_engine, event, inspect
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from fin_intel.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    if not (url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:")):
        return create_engine(url)
    Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record) -> None:
        # WAL lets the API read while a sync writes; foreign keys are off by default.
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


@lru_cache
def get_engine() -> Engine:
    return make_engine(get_settings().database_url)


MIGRATIONS = Path(__file__).parent / "migrations"
# Databases created before migrations existed match this revision.
BASELINE_REVISION = "0001"


def alembic_config(connection: Connection | None = None) -> AlembicConfig:
    config = AlembicConfig()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["connection"] = connection
    return config


def init_db(engine: Engine | None = None) -> None:
    """Bring the database schema up to date by running pending migrations."""
    engine = engine or get_engine()
    sqlite = engine.dialect.name == "sqlite"
    with engine.begin() as connection:
        if sqlite:
            # SQLite migrations rebuild tables (copy, drop, rename); enforced foreign keys
            # would block dropping a referenced table. Must run before the transaction starts.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        config = alembic_config(connection)
        tables = inspect(connection)
        if tables.has_table("securities") and not tables.has_table("alembic_version"):
            command.stamp(config, BASELINE_REVISION)
        command.upgrade(config, "head")
    if sqlite:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")


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
    dialect = session.get_bind().dialect.name
    insert = {"postgresql": postgresql.insert, "sqlite": sqlite.insert}.get(dialect)
    if insert is None:
        raise NotImplementedError(f"upsert not supported for dialect {dialect!r}")
    stmt = insert(model.__table__)
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
