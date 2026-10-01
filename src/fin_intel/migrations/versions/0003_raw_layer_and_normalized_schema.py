"""raw layer and normalized schema

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The old denormalized facts table goes first: SQLite index names are database-wide and
    # the new table reuses ix_facts_cik_concept. Its contents are re-synced from SEC, which
    # also captures them in the new raw layer.
    with op.batch_alter_table("financial_facts", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_facts_cik_concept"))
    op.drop_table("financial_facts")

    op.create_table(
        "concepts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("taxonomy", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("label", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("taxonomy", "name"),
    )
    op.create_table(
        "issuers",
        sa.Column("cik", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("name", sa.String(length=256), nullable=True),
        sa.PrimaryKeyConstraint("cik"),
    )
    op.create_table(
        "raw_responses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=True),
        sa.Column("params", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("raw_responses", schema=None) as batch_op:
        batch_op.create_index(
            "ix_raw_lookup", ["provider", "dataset", "key", "fetched_at"], unique=False
        )

    op.create_table(
        "sync_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("job", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("items_ok", sa.Integer(), nullable=False),
        sa.Column("items_failed", sa.Integer(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sync_state",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("last_attempt", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("rows", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("provider", "dataset", "key"),
    )
    op.create_table(
        "filings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("accession", sa.String(length=32), nullable=False),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("form", sa.String(length=16), nullable=True),
        sa.Column("filed", sa.Date(), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=8), nullable=True),
        sa.Column("report_period_end", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(
            ["cik"],
            ["issuers.cik"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accession"),
    )
    with op.batch_alter_table("filings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_filings_cik"), ["cik"], unique=False)

    op.create_table(
        "fiscal_calendars",
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("segment", sa.Integer(), nullable=False),
        sa.Column("year_end_month", sa.Integer(), nullable=False),
        sa.Column("year_end_day", sa.Integer(), nullable=False),
        sa.Column("year_offset", sa.Integer(), nullable=False),
        sa.Column("first_year_end", sa.Date(), nullable=False),
        sa.Column("last_year_end", sa.Date(), nullable=False),
        sa.ForeignKeyConstraint(
            ["cik"],
            ["issuers.cik"],
        ),
        sa.PrimaryKeyConstraint("cik", "segment"),
    )
    op.create_table(
        "corporate_actions",
        sa.Column("security_id", sa.Integer(), nullable=False),
        sa.Column("ex_date", sa.Date(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
        ),
        sa.PrimaryKeyConstraint("security_id", "ex_date", "action", "source"),
    )
    op.create_table(
        "facts",
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("concept_id", sa.Integer(), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("instant", sa.Boolean(), nullable=False),
        sa.Column("value", sa.Double(), nullable=False),
        sa.Column("frame", sa.String(length=16), nullable=True),
        sa.Column("cik", sa.Integer(), nullable=False),
        sa.Column("period_type", sa.String(length=16), nullable=True),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=8), nullable=True),
        sa.ForeignKeyConstraint(
            ["cik"],
            ["issuers.cik"],
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"],
            ["concepts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["filings.id"],
        ),
        sa.PrimaryKeyConstraint("filing_id", "concept_id", "unit", "period_start", "period_end"),
    )
    with op.batch_alter_table("facts", schema=None) as batch_op:
        batch_op.create_index("ix_facts_cik_concept", ["cik", "concept_id"], unique=False)

    # Issuers from the CIKs securities already reference.
    op.execute(
        "INSERT INTO issuers (cik, name) SELECT cik, MIN(name) FROM securities "
        "WHERE cik IS NOT NULL GROUP BY cik"
    )
    # Splits and dividends move out of the bars before those columns are dropped. Tiingo
    # stores split ratios with float noise (7.000007), hence the rounding.
    op.execute(
        "INSERT INTO corporate_actions (security_id, ex_date, action, source, value) "
        "SELECT security_id, date, 'split', source, ROUND(split_factor, 4) FROM daily_bars "
        "WHERE split_factor != 1"
    )
    op.execute(
        "INSERT INTO corporate_actions (security_id, ex_date, action, source, value) "
        "SELECT security_id, date, 'dividend', source, dividend FROM daily_bars "
        "WHERE dividend != 0"
    )
    with op.batch_alter_table("daily_bars", schema=None) as batch_op:
        batch_op.drop_column("adj_high")
        batch_op.drop_column("split_factor")
        batch_op.drop_column("adj_open")
        batch_op.drop_column("adj_volume")
        batch_op.drop_column("adj_low")
        batch_op.drop_column("adj_close")
        batch_op.drop_column("dividend")

    with op.batch_alter_table("securities", schema=None) as batch_op:
        batch_op.create_foreign_key("fk_securities_cik_issuers", "issuers", ["cik"], ["cik"])

    # ### end Alembic commands ###


def downgrade() -> None:
    # Adjusted price columns and the old facts table can't be reconstructed here.
    raise NotImplementedError("0003 is irreversible; restore a backup instead")
