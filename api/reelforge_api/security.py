"""Password hashing and one-time link tokens (docs/TECHNICAL_DESIGN.md, Security)."""

from __future__ import annotations

import hashlib
import secrets
from functools import lru_cache
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()  # argon2id with the library's recommended costs

# Checked against when a login names an email that doesn't exist, so that request
# takes as long as one for a real account and the two can't be told apart by timing.
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


@lru_cache(maxsize=1)
def _common_passwords() -> frozenset[str]:
    path = Path(__file__).with_name("common_passwords.txt")
    return frozenset(line.strip().lower() for line in path.read_text("utf-8").splitlines() if line.strip())


def is_common_password(password: str) -> bool:
    return password.lower() in _common_passwords()


def new_token() -> str:
    """A random value for an emailed link. Unguessable: 256 bits."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """What is stored for a link token, so a copy of the database can't be used to follow links."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
