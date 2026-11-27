"""Request bodies and the User object of the API (docs/API_DESIGN.md).

JSON uses camelCase; Python uses snake_case. Unknown fields are rejected.
"""

from __future__ import annotations

import unicodedata
from datetime import UTC
from typing import Annotated, Any, Literal, TypeVar

from flask import request
from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, ValidationError
from pydantic.alias_generators import to_camel

from .errors import ApiError
from .models import User
from .security import is_common_password

PASSWORD_MIN, PASSWORD_MAX = 8, 128
NAME_MAX = 80
NICHE_MAX, NICHE_TOPICS_MAX, NICHE_TOPIC_MAX = 120, 3, 40
CAPTION_STYLES = ("bold-pop", "clean", "neon")
MODES = ("faceless", "full_ai")


class Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, extra="forbid", strict=True)


# ---------------------------------------------------------------- field rules


def _clean_text(value: str) -> str:
    """Trims, and refuses control characters (line breaks, tabs, NUL) in one-line text."""
    _storable(value)
    value = unicodedata.normalize("NFC", value).strip()
    if any(unicodedata.category(ch) == "Cc" for ch in value):
        raise ValueError("Remove line breaks and other special characters")
    return value


def _storable(value: str) -> str:
    """Refuses text that can't be stored: a NUL character, or half of a character pair
    (what is left when an emoji is cut in two)."""
    if "\x00" in value or any(unicodedata.category(ch) == "Cs" for ch in value):
        raise ValueError("That contains characters that can't be used")
    return value


def _name(value: str) -> str:
    value = _clean_text(value)
    if not value:
        raise ValueError("Enter your name")
    if len(value) > NAME_MAX:
        raise ValueError(f"Use {NAME_MAX} characters or fewer")
    return value


def _new_password(value: str) -> str:
    if len(value) < PASSWORD_MIN:
        raise ValueError(f"Use at least {PASSWORD_MIN} characters")
    if len(value) > PASSWORD_MAX:
        raise ValueError(f"Use {PASSWORD_MAX} characters or fewer")
    _storable(value)
    if is_common_password(value):
        raise ValueError("That password is too common. Choose one that is harder to guess")
    return value


def _email(value: str) -> str:
    if len(value) > 254:
        raise ValueError("Enter a valid email address")
    return value.strip().lower()


def _login_email(value: str) -> str:
    # Not checked as an email address (the answer for an unknown one is the same as for a
    # wrong password), only for text the database can't hold.
    try:
        return _clean_text(value)
    except ValueError:
        raise ValueError("Enter a valid email address") from None


def _only_true(value: bool) -> bool:
    if value is not True:
        raise ValueError("That value isn't allowed")
    return value


def _niche(value: str) -> str:
    """Up to three topics separated by commas, no repeats (API A10). Stored as "A, B, C"."""
    value = _clean_text(value)
    if len(value) > NICHE_MAX:
        raise ValueError(f"Use {NICHE_MAX} characters or fewer")
    topics = [" ".join(part.split()) for part in value.split(",")]
    topics = [t for t in topics if t]
    if len(topics) > NICHE_TOPICS_MAX:
        raise ValueError(f"Choose up to {NICHE_TOPICS_MAX} topics")
    if any(len(t) > NICHE_TOPIC_MAX for t in topics):
        raise ValueError(f"Each topic can have up to {NICHE_TOPIC_MAX} characters")
    if len({t.casefold() for t in topics}) != len(topics):
        raise ValueError("Each topic can only be chosen once")
    return ", ".join(topics)


def _voice_id(value: str) -> str:
    """One of the voices in GET /voices (D3), or "" for the default voice."""
    from .voices import is_known

    value = _clean_text(value)
    if value and not is_known(value):
        raise ValueError("That voice isn't available")
    return value


def _plain_address(value: object) -> object:
    # The email library also accepts "Name <a@b.com>" and keeps only the address. The form
    # asks for an address, and login compares the text as typed, so that form is refused.
    if isinstance(value, str) and (set(value) & set('<>"') or value != value.strip() or " " in value):
        raise ValueError("Enter a valid email address")
    return value


Email = Annotated[EmailStr, BeforeValidator(_plain_address), AfterValidator(_email)]
NewPassword = Annotated[str, AfterValidator(_new_password)]
# What the user typed at login or as their current password: any text, bounded in size.
TypedPassword = Annotated[str, Field(min_length=1, max_length=1024)]
LinkToken = Annotated[str, Field(min_length=1, max_length=200)]


# ---------------------------------------------------------------- bodies


class SignupBody(Body):
    email: Email
    password: NewPassword
    name: Annotated[str, AfterValidator(_name)]


class LoginBody(Body):
    email: Annotated[str, Field(min_length=1, max_length=254), AfterValidator(_login_email)]
    password: TypedPassword


class TokenBody(Body):
    token: LinkToken


class ForgotPasswordBody(Body):
    email: Email


class ResetPasswordBody(Body):
    token: LinkToken
    password: NewPassword


class DefaultsBody(Body):
    voice_id: Annotated[str, AfterValidator(_voice_id)] | None = None
    caption_style: Literal["bold-pop", "clean", "neon"] | None = None
    mode: Literal["faceless", "full_ai"] | None = None


class UpdateMeBody(Body):
    name: Annotated[str, AfterValidator(_name)] | None = None
    niche: Annotated[str, AfterValidator(_niche)] | None = None
    defaults: DefaultsBody | None = None
    onboarding_completed: Annotated[bool, AfterValidator(_only_true)] | None = None


class ChangePasswordBody(Body):
    current_password: TypedPassword
    new_password: NewPassword


class DeleteMeBody(Body):
    password: TypedPassword


# ---------------------------------------------------------------- reading a body

_MESSAGES = {
    "missing": "Required",
    "extra_forbidden": "This field isn't accepted",
    "string_type": "Must be text",
    "bool_type": "Must be true or false",
    "model_type": "Must be an object",
    "model_attributes_type": "Must be an object",
    "string_too_short": "Required",
    "string_too_long": "Too long",
    "literal_error": "That value isn't allowed",
    "greater_than_equal": "Too small",
    "greater_than": "Too small",
    "less_than_equal": "Too large",
    "less_than": "Too large",
    "int_type": "Must be a whole number",
    "float_type": "Must be a number",
    "finite_number": "Must be a number",
    "list_type": "Must be a list",
    "too_long": "Too many items",
    "too_short": "Too few items",
    "uuid_type": "Not a valid id",
    "uuid_parsing": "Not a valid id",
}

B = TypeVar("B", bound=Body)


def _field_errors(error: ValidationError) -> dict[str, str]:
    fields: dict[str, str] = {}
    for item in error.errors():
        name = ".".join(str(part) for part in item["loc"]) or "body"
        kind = item["type"]
        context = item.get("ctx") or {}
        if kind == "value_error" and "error" in context:
            message = str(context["error"])  # raised by one of the rules above
        elif kind == "value_error" and "reason" in context:
            message = "Enter a valid email address"  # the email library's own errors
        else:
            message = _MESSAGES.get(kind, "That value isn't valid")
        fields.setdefault(name, message)
    return fields


def read_body(model: type[B], *, allow_empty: bool = False) -> B:
    """The request's JSON body as `model`, or 400 validation_error with details.fields.
    `allow_empty`: for endpoints whose body is optional, no body counts as `{}`."""
    if allow_empty and not request.content_length and not request.mimetype:
        data: Any = {}
    else:
        # A body in another format (a form, plain text) is 415. No body at all is a 400 below.
        if not request.is_json and (request.content_length or request.mimetype):
            raise ApiError(415, "unsupported_media_type", "Send the details as JSON.")
        data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError(400, "validation_error", "Send the details as a JSON object.", {"fields": {}})
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise ApiError(
            400, "validation_error", "Some details need another look.", {"fields": _field_errors(e)}
        ) from None


# ---------------------------------------------------------------- the User object


def _iso(value) -> str | None:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def user_json(user: User) -> dict[str, Any]:
    from .connections import connection_json

    credits = user.credit_account
    instagram = user.instagram_account
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "niche": user.niche,
        "emailVerified": user.email_verified_at is not None,
        "defaults": {
            "voiceId": user.default_voice_id,
            "captionStyle": user.default_caption_style,
            "mode": user.default_mode,
        },
        "onboardingCompleted": user.onboarding_completed_at is not None,
        "isAdmin": user.is_admin,
        "credits": {
            "balance": credits.balance if credits else 0,
            "monthlyAllowance": credits.monthly_allowance if credits else 0,
            "resetsAt": _iso(credits.resets_at) if credits else None,
        },
        # Publishing accounts (B6): YouTube first; Instagram is null until B6b.
        "connections": {"youtube": connection_json(user.youtube_account), "instagram": None},
        "instagram": {
            "connected": instagram is not None,
            "username": instagram.username if instagram else None,
            "status": instagram.status if instagram else None,
            "expiresAt": _iso(instagram.token_expires_at) if instagram else None,
        },
    }
