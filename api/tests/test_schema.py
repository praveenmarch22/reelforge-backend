"""The migrations build the schema in docs/DB_DESIGN.md, and its rules hold."""

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

EXPECTED_TABLES = {
    "users",
    "auth_tokens",
    "refresh_tokens",
    "instagram_accounts",
    "credit_accounts",
    "credit_ledger",
    "projects",
    "scenes",
    "assets",
    "music_tracks",
    "jobs",
    "renders",
    "posts",
    "provider_switches",
    "idempotency_keys",
    "usage_daily",
    "youtube_accounts",
    "autopilot_settings",
    "autopilot_topics",
}


def table_names(session) -> set[str]:
    rows = session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
    return {r[0] for r in rows}


def test_all_nineteen_tables_exist(session):
    assert table_names(session) - {"alembic_version"} == EXPECTED_TABLES


def test_database_is_at_the_latest_migration(session):
    assert session.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0003"


def new_user(session, email="a@example.com"):
    return session.execute(
        text("INSERT INTO users (email, password_hash) VALUES (:e, 'x') RETURNING id"), {"e": email}
    ).scalar_one()


def test_email_is_unique_ignoring_case(session):
    new_user(session, "Creator@Example.com")
    with pytest.raises(IntegrityError):
        new_user(session, "creator@example.com")


def test_credit_balance_cannot_go_negative(session):
    user = new_user(session)
    session.execute(
        text("INSERT INTO credit_accounts (user_id, balance, resets_at) VALUES (:u, 5, now())"), {"u": user}
    )
    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE credit_accounts SET balance = balance - 6 WHERE user_id = :u"), {"u": user}
        )


def test_only_one_active_job_per_scene_and_type(session):
    user = new_user(session)
    project = session.execute(
        text("INSERT INTO projects (user_id) VALUES (:u) RETURNING id"), {"u": user}
    ).scalar_one()
    scene = session.execute(
        text("INSERT INTO scenes (project_id, position, narration) VALUES (:p, 1, 'hi') RETURNING id"),
        {"p": project},
    ).scalar_one()
    insert = text("INSERT INTO jobs (user_id, project_id, scene_id, type) VALUES (:u, :p, :s, 'voice')")
    session.execute(insert, {"u": user, "p": project, "s": scene})
    with pytest.raises(IntegrityError):
        session.execute(insert, {"u": user, "p": project, "s": scene})


def test_updated_at_moves_on_update(session):
    user = new_user(session)
    session.commit()  # the trigger uses now(), which only advances between transactions
    try:
        session.execute(text("UPDATE users SET name = 'New' WHERE id = :u"), {"u": user})
        created, updated = session.execute(
            text("SELECT created_at, updated_at FROM users WHERE id = :u"), {"u": user}
        ).one()
        assert updated > created
    finally:
        session.rollback()
        session.execute(text("DELETE FROM users WHERE id = :u"), {"u": user})
        session.commit()


def test_migrations_can_be_undone_and_reapplied(app):
    with app.app_context():
        from reelforge_api.extensions import db

        downgrade(revision="base")
        assert table_names(db.session) - {"alembic_version"} == set()
        db.session.rollback()
        upgrade()
        assert table_names(db.session) - {"alembic_version"} == EXPECTED_TABLES
        db.session.rollback()
