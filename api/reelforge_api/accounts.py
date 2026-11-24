"""Account operations shared by the auth and profile endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from flask import current_app
from sqlalchemy import select, text, update

from .extensions import db
from .models import AuthToken, CreditAccount, CreditLedger, InstagramAccount, User
from .security import hash_password, hash_token, new_token
from .sessions import now, revoke_all_sessions

VERIFY_EMAIL, RESET_PASSWORD = "verify_email", "reset_password"  # noqa: S105
TOKEN_LIFETIME = {VERIFY_EMAIL: timedelta(hours=24), RESET_PASSWORD: timedelta(hours=1)}


def find_user_by_email(email: str) -> User | None:
    """The account with this email, unless it has been deleted."""
    return db.session.execute(
        select(User).where(User.email == email.strip(), User.deleted_at.is_(None))
    ).scalar_one_or_none()


def next_month_start(moment: datetime) -> datetime:
    first = moment.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        first.replace(year=first.year + 1, month=1)
        if first.month == 12
        else first.replace(month=first.month + 1)
    )


def create_user(email: str, password: str, name: str) -> User:
    """Adds the user and their credit account with the first month's credits. Caller commits."""
    settings = current_app.extensions["settings"]
    user = User(email=email, password_hash=hash_password(password), name=name)
    db.session.add(user)
    db.session.flush()  # gives the user its id
    allowance = settings.monthly_credits
    db.session.add(
        CreditAccount(
            user_id=user.id, balance=allowance, monthly_allowance=allowance, resets_at=next_month_start(now())
        )
    )
    if allowance:
        db.session.add(
            CreditLedger(user_id=user.id, amount=allowance, kind="grant", note="Monthly allowance")
        )
    db.session.flush()
    db.session.refresh(user)
    return user


def issue_token(user: User, kind: str) -> str:
    """A new one-time link token; earlier unused ones of the same kind stop working. Caller commits."""
    db.session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.kind == kind, AuthToken.used_at.is_(None))
        .values(used_at=now())
    )
    token = new_token()
    db.session.add(
        AuthToken(
            user_id=user.id, kind=kind, token_hash=hash_token(token), expires_at=now() + TOKEN_LIFETIME[kind]
        )
    )
    return token


def use_token(token: str, kind: str) -> User | None:
    """Marks a link token used and returns its user; None if it is unknown, used or expired."""
    row = db.session.execute(
        select(AuthToken)
        .where(AuthToken.token_hash == hash_token(token), AuthToken.kind == kind)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None or row.used_at is not None or row.expires_at <= now():
        return None
    user = db.session.get(User, row.user_id)
    if user is None or user.deleted_at is not None:
        return None
    row.used_at = now()
    return user


def set_password(user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    # A reset link requested before the change must not undo it.
    db.session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.kind == RESET_PASSWORD, AuthToken.used_at.is_(None))
        .values(used_at=now())
    )


def soft_delete(user: User) -> None:
    """API A12: the account stops working now and is purged after 7 days. Caller commits.

    The email address is released at once, so the person can sign up again with it straight
    away. The row keeps a placeholder address (unique, and never a deliverable one) until
    the purge removes it.
    """
    user.deleted_at = now()
    user.email = f"deleted-{user.id}@deleted.invalid"
    revoke_all_sessions(user.id)
    db.session.execute(
        update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.used_at.is_(None))
        .values(used_at=now())
    )
    db.session.query(InstagramAccount).filter_by(user_id=user.id).delete()
    db.session.execute(
        text(
            "UPDATE jobs SET state = 'cancelled', finished_at = now() "
            "WHERE user_id = :user_id AND state IN ('queued', 'running')"
        ),
        {"user_id": user.id},
    )
