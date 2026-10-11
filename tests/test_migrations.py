from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from fin_intel.db import Base


def test_migrations_match_models(engine):
    # `engine` is built by running every migration; models must not have drifted from them.
    with engine.connect() as conn:
        context = MigrationContext.configure(conn)
        diff = compare_metadata(context, Base.metadata)
    assert diff == []
