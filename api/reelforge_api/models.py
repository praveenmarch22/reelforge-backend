"""Database models for the tables the API has code for so far.

The tables themselves are created by the migrations (docs/schema.sql is the agreed target);
these classes only describe them to SQLAlchemy. Columns with a database default
(ids, created_at, updated_at) are left to the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, CITEXT, INET, TIMESTAMP, UUID
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
