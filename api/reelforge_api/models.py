"""Database models for the tables the API has code for so far.

The tables themselves are created by the migrations (docs/schema.sql is the agreed target);
these classes only describe them to SQLAlchemy. Columns with a database default
(ids, created_at, updated_at) are left to the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, LargeBinary, Numeric, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .extensions import db

_uuid_pk = lambda: mapped_column(  # noqa: E731
    UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
)
_now = lambda: mapped_column(TIMESTAMP(timezone=True), server_default=text("now()"))  # noqa: E731
_time = lambda: mapped_column(TIMESTAMP(timezone=True))  # noqa: E731


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = _time()
    name: Mapped[str] = mapped_column(Text, server_default="")
    niche: Mapped[str] = mapped_column(Text, server_default="")
    default_voice_id: Mapped[str] = mapped_column(Text, server_default="")
    default_caption_style: Mapped[str] = mapped_column(Text, server_default="bold-pop")
    default_mode: Mapped[str] = mapped_column(Text, server_default="faceless")
    onboarding_completed_at: Mapped[datetime | None] = _time()
    is_admin: Mapped[bool] = mapped_column(server_default=text("false"))
    is_tester: Mapped[bool] = mapped_column(server_default=text("false"))
    disabled_at: Mapped[datetime | None] = _time()
    last_login_at: Mapped[datetime | None] = _time()
    deleted_at: Mapped[datetime | None] = _time()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    credit_account: Mapped[CreditAccount | None] = relationship(uselist=False, lazy="joined")
    instagram_account: Mapped[InstagramAccount | None] = relationship(uselist=False, lazy="joined")


class AuthToken(db.Model):
    """One-time token for an email verification or password reset link. Only its hash is stored."""

    __tablename__ = "auth_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = _time()
    used_at: Mapped[datetime | None] = _time()
    created_at: Mapped[datetime] = _now()


class RefreshToken(db.Model):
    """One issued refresh token. All rotations of one login share a family_id."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    jti: Mapped[str] = mapped_column(Text, unique=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    user_agent: Mapped[str] = mapped_column(Text, server_default="")
    ip_address: Mapped[str | None] = mapped_column(INET)
    expires_at: Mapped[datetime] = _time()
    revoked_at: Mapped[datetime | None] = _time()
    created_at: Mapped[datetime] = _now()


class CreditAccount(db.Model):
    __tablename__ = "credit_accounts"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    monthly_allowance: Mapped[int] = mapped_column(Integer, server_default=text("100"))
    resets_at: Mapped[datetime] = _time()
    updated_at: Mapped[datetime] = _now()


class CreditLedger(db.Model):
    """Every change to a balance. The balance never changes without a row here."""

    __tablename__ = "credit_ledger"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    amount: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(Text)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    note: Mapped[str] = mapped_column(Text, server_default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _now()


class InstagramAccount(db.Model):
    __tablename__ = "instagram_accounts"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    ig_user_id: Mapped[str] = mapped_column(Text)
    username: Mapped[str] = mapped_column(Text)
    profile_picture_url: Mapped[str] = mapped_column(Text, server_default="")
    account_type: Mapped[str] = mapped_column(Text)
    access_token_enc: Mapped[bytes] = mapped_column(LargeBinary)
    token_expires_at: Mapped[datetime] = _time()
    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    status: Mapped[str] = mapped_column(Text, server_default="connected")
    last_refreshed_at: Mapped[datetime | None] = _time()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()


# ---------------------------------------------------------------- projects (milestone B3)


class Project(db.Model):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text, server_default="Untitled Reel")
    topic: Mapped[str] = mapped_column(Text, server_default="")
    source_script: Mapped[str | None] = mapped_column(Text)
    tone: Mapped[str] = mapped_column(Text, server_default="educational")
    mode: Mapped[str] = mapped_column(Text, server_default="faceless")
    target_seconds: Mapped[int] = mapped_column(Integer, server_default=text("30"))
    style: Mapped[str] = mapped_column(Text, server_default="cinematic")
    character: Mapped[str] = mapped_column(Text, server_default="")
    voice_id: Mapped[str] = mapped_column(Text, server_default="")
    music_mood: Mapped[str] = mapped_column(Text, server_default="calm")
    music_track_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    music_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    settings: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, server_default="scripting")
    timeline: Mapped[dict | None] = mapped_column(JSONB)
    timeline_version: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    post_caption: Mapped[str] = mapped_column(Text, server_default="")
    hashtags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    thumbnail_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    deleted_at: Mapped[datetime | None] = _time()
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    scenes: Mapped[list[Scene]] = relationship(order_by="Scene.position", passive_deletes=True)


class Asset(db.Model):
    """Every stored file. Written by the generation tasks (milestone B4)."""

    __tablename__ = "assets"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="ready")
    s3_key: Mapped[str] = mapped_column(Text, unique=True)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Numeric(8, 3, asdecimal=False))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    replaced_at: Mapped[datetime | None] = _time()
    created_at: Mapped[datetime] = _now()


class Scene(db.Model):
    __tablename__ = "scenes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    narration: Mapped[str] = mapped_column(Text)
    on_screen_text: Mapped[str] = mapped_column(Text, server_default="")
    visual_prompt: Mapped[str] = mapped_column(Text, server_default="")
    stock_query: Mapped[str] = mapped_column(Text, server_default="")
    mood: Mapped[str] = mapped_column(Text, server_default="calm")
    visual_source: Mapped[str] = mapped_column(Text, server_default="stock")
    voice_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    visual_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    keyframe_asset_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assets.id", ondelete="SET NULL"))
    words: Mapped[list] = mapped_column(JSONB, server_default=text("'[]'"))
    words_edited: Mapped[bool] = mapped_column(server_default=text("false"))
    trim_start: Mapped[float] = mapped_column(Numeric(6, 3, asdecimal=False), server_default=text("0"))
    trim_end: Mapped[float | None] = mapped_column(Numeric(6, 3, asdecimal=False))
    voice_status: Mapped[str] = mapped_column(Text, server_default="missing")
    visual_status: Mapped[str] = mapped_column(Text, server_default="missing")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = _now()

    # Loaded together with the scenes: the project is polled every 2 s while the editor is open.
    voice_asset: Mapped[Asset | None] = relationship(foreign_keys=[voice_asset_id], lazy="selectin")
    visual_asset: Mapped[Asset | None] = relationship(foreign_keys=[visual_asset_id], lazy="selectin")


class Job(db.Model):
    """One unit of background work (DB design 3.11)."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    scene_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("scenes.id", ondelete="CASCADE"))
    type: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, server_default="queued")
    progress: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    provider: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    celery_task_id: Mapped[str | None] = mapped_column(Text)
    credits_reserved: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cost_estimate_usd: Mapped[float] = mapped_column(
        Numeric(10, 5, asdecimal=False), server_default=text("0")
    )
    input: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'"))
    output: Mapped[dict | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    queued_at: Mapped[datetime] = _now()
    started_at: Mapped[datetime | None] = _time()
    finished_at: Mapped[datetime | None] = _time()


class MusicTrack(db.Model):
    """The royalty-free music library (filled in milestone B4)."""

    __tablename__ = "music_tracks"

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(server_default=text("true"))


class ProviderSwitch(db.Model):
    """The admin kill switch for a provider (G-3). No row means switched on."""

    __tablename__ = "provider_switches"

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    enabled: Mapped[bool] = mapped_column(server_default=text("true"))
    reason: Mapped[str] = mapped_column(Text, server_default="")
