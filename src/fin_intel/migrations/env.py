from alembic import context

from fin_intel import models  # noqa: F401  (registers tables)
from fin_intel.config import get_settings
from fin_intel.db import Base, make_engine

config = context.config


def run_migrations(connection) -> None:
    # Batch mode lets SQLite (which can't ALTER most things) copy-and-replace tables.
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


if (connection := config.attributes.get("connection")) is not None:
    run_migrations(connection)
else:
    with make_engine(get_settings().database_url).begin() as connection:
        run_migrations(connection)
