"""Encrypting tokens kept for users (YouTube's access and refresh tokens; milestone B6).

Fernet (AES with an HMAC) with the key in TOKEN_ENCRYPTION_KEY. A token that can't be
decrypted (the key changed) counts as missing: the creator connects again.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _fernet() -> Fernet:
    return Fernet(current_app.extensions["settings"].token_encryption_key)


def encrypt(value: str) -> bytes:
    return _fernet().encrypt(value.encode("utf-8"))


def decrypt(value: bytes) -> str | None:
    try:
        return _fernet().decrypt(bytes(value)).decode("utf-8")
    except InvalidToken:
        return None
