"""Baseline: the 16 tables from docs/DB_DESIGN.md.

Runs 0001_baseline.sql, an exact copy of docs/schema.sql (the schema that was tested on
PostgreSQL 16). CI fails if the two files differ. Never edit either file after this
migration has been applied anywhere: schema changes go in new migrations.

Revision ID: 0001
Revises:
"""

from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = [
    "usage_daily", "idempotency_keys", "provider_switches", "posts", "renders", "credit_ledger",
    "jobs", "scenes", "assets", "projects", "music_tracks", "credit_accounts",
    "instagram_accounts", "refresh_tokens", "auth_tokens", "users",
]


def upgrade():
    sql = Path(__file__).with_name("0001_baseline.sql").read_text(encoding="utf-8")
    # Sent as-is through the driver: the script has several statements and a function body,
    # and its JSON defaults contain ":" which SQLAlchemy's text() would read as parameters.
    op.get_bind().exec_driver_sql(sql)


def downgrade():
    bind = op.get_bind()
    for table in TABLES:
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS {table} CASCADE")
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS set_updated_at()")
