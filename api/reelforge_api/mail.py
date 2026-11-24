"""Outgoing email: the verification and password reset messages.

Messages are sent by a background task so a slow mail server never slows a request down
and a failed send is retried. Which service sends them is a setting (EMAIL_PROVIDER).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import asdict, dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from urllib.parse import urlencode

from flask import current_app

log = logging.getLogger(__name__)

# Filled only when EMAIL_PROVIDER=memory (tests read the messages from here).
outbox: list[Email] = []


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str
    html: str


def _settings():
    return current_app.extensions["settings"]


def _link(path: str, token: str) -> str:
    return f"{_settings().web_origin.rstrip('/')}{path}?{urlencode({'token': token})}"


def _layout(heading: str, body: str, button: str, link: str, footer: str) -> str:
    return (
        '<div style="font-family:Arial,Helvetica,sans-serif;max-width:480px;margin:0 auto;'
        'padding:24px;color:#111">'
        f'<h1 style="font-size:20px;margin:0 0 12px">{heading}</h1>'
        f'<p style="font-size:15px;line-height:1.5;margin:0 0 20px">{body}</p>'
        f'<p style="margin:0 0 20px"><a href="{link}" style="display:inline-block;background:#7c3aed;'
        f'color:#fff;text-decoration:none;padding:12px 20px;border-radius:8px;font-size:15px">'
        f"{button}</a></p>"
        f'<p style="font-size:13px;line-height:1.5;color:#555;margin:0">{footer}</p></div>'
    )


def verification_email(to: str, token: str) -> Email:
    link = _link("/verify", token)
    return Email(
        to=to,
        subject="Verify your email for ReelForge",
        text=(
            "Welcome to ReelForge.\n\n"
            f"Open this link to verify your email address:\n{link}\n\n"
            "The link works once and expires in 24 hours. "
            "If you didn't create a ReelForge account, you can ignore this email."
        ),
        html=_layout(
            "Verify your email",
            "Welcome to ReelForge. Confirm this is your email address to start creating Reels.",
            "Verify email",
            link,
            "The link works once and expires in 24 hours. If you didn't create a ReelForge account, "
            "you can ignore this email.",
        ),
    )


def reset_email(to: str, token: str) -> Email:
    link = _link("/reset", token)
    return Email(
        to=to,
        subject="Reset your ReelForge password",
        text=(
            "We received a request to reset your ReelForge password.\n\n"
            f"Open this link to choose a new one:\n{link}\n\n"
            "The link works once and expires in 1 hour. "
            "If you didn't ask for this, you can ignore this email; your password stays the same."
        ),
        html=_layout(
            "Reset your password",
            "We received a request to reset your ReelForge password.",
            "Choose a new password",
            link,
            "The link works once and expires in 1 hour. If you didn't ask for this, you can ignore "
            "this email; your password stays the same.",
        ),
    )


def send_now(email: Email) -> None:
    """Hands one message to the configured service. Raises if the service refuses it."""
    s = _settings()
    if s.email_provider == "memory":
        outbox.append(email)
        return
    if s.email_provider == "console":
        # Development without a mail server: the message (with its link) goes to the log.
        log.info("email (not sent, EMAIL_PROVIDER=console)", extra={"to": email.to, "subject": email.subject})
        print(f"\n--- email to {email.to}: {email.subject} ---\n{email.text}\n---\n", flush=True)  # noqa: T201
        return

    message = EmailMessage()
    message["From"] = s.email_from
    message["To"] = email.to
    message["Subject"] = email.subject
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain="reelforge.mail")
    message.set_content(email.text)
    message.add_alternative(email.html, subtype="html")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10) as smtp:
        if s.smtp_starttls:
            # Encrypts the connection and checks the mail server's certificate.
            smtp.starttls(context=ssl.create_default_context())
        if s.smtp_username:
            smtp.login(s.smtp_username, s.smtp_password)
        smtp.send_message(message)


def send_password_reset(address: str) -> None:
    """Emails a reset link if this address has a usable account; otherwise does nothing."""
    from . import accounts
    from .extensions import db

    user = accounts.find_user_by_email(address)
    if user is None or user.disabled_at is not None:
        return
    token = accounts.issue_token(user, accounts.RESET_PASSWORD)
    db.session.commit()
    send_now(reset_email(user.email, token))


def request_password_reset(address: str) -> None:
    """API A7. Hands the whole job to the worker, so the request reveals nothing by its timing."""
    if _settings().env == "test":
        send_password_reset(address)  # tests have no worker for this; do it in place
        return
    from .tasks.mail import password_reset

    try:
        password_reset.delay(address)
    except Exception:  # noqa: BLE001
        log.exception("could not queue password reset")


def deliver(email: Email) -> None:
    """Queues a message for sending. Call after the database commit it belongs to."""
    if _settings().env == "test":
        send_now(email)  # tests have no worker for this; send in place
        return
    from .tasks.mail import send_email

    try:
        send_email.delay(asdict(email))
    except Exception:  # noqa: BLE001
        # The queue is unreachable. The account action itself succeeded; the user can ask
        # for the email again, so this is logged rather than failing the request.
        log.exception("could not queue email", extra={"subject": email.subject})
