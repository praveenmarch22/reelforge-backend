"""Sending email: the message a mail server receives, the encrypted connection, and the
hand-over to the background worker (docs/TECHNICAL_DESIGN.md Security; docs/API_DESIGN.md A1, A7).

The SMTP tests talk to a small mail server that runs inside the test, over a real connection.
"""

from __future__ import annotations

import base64
import email
import email.policy
import re
import shutil
import smtplib
import socketserver
import ssl
import subprocess
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

import pytest
from kombu.exceptions import OperationalError
from sqlalchemy import event

from reelforge_api import mail
from reelforge_api.extensions import db

from .conftest import build_app
from .support import NEW_PASSWORD, PASSWORD, Browser, link_token, new_email, sign_up

# ---------------------------------------------------------------- a mail server for the tests


class _Conversation(socketserver.StreamRequestHandler):
    """Just enough SMTP for smtplib: EHLO, STARTTLS, AUTH PLAIN, MAIL, RCPT, DATA, QUIT."""

    def reply(self, line: str) -> None:
        self.wfile.write(line.encode() + b"\r\n")

    def handle(self) -> None:
        server: MailServer = self.server
        encrypted = False
        self.reply("220 test mail server")
        while line := self.rfile.readline():
            command, _, argument = line.decode().strip().partition(" ")
            command = command.upper()
            if command == "EHLO":
                self.reply("250-test mail server")
                if server.tls and not encrypted:
                    self.reply("250-STARTTLS")
                self.reply("250 AUTH PLAIN")
            elif command == "STARTTLS" and server.tls:
                self.reply("220 ready")
                try:
                    secure = server.tls.wrap_socket(self.connection, server_side=True)
                except OSError:  # the client did not accept the certificate
                    return
                self.connection = secure
                self.rfile, self.wfile = secure.makefile("rb"), secure.makefile("wb", buffering=0)
                encrypted = True
            elif command == "AUTH":
                _, username, password = base64.b64decode(argument.split(" ", 1)[1]).decode().split("\0")
                server.logins.append(Login(encrypted, username, password))
                self.reply("235 welcome")
            elif command in ("MAIL", "RCPT"):
                self.reply("250 ok")
            elif command == "DATA":
                self.reply("354 go on")
                data = b""
                while (part := self.rfile.readline()) not in (b".\r\n", b""):
                    data += part[1:] if part.startswith(b"..") else part
                received = email.message_from_bytes(data, policy=email.policy.default)
                server.deliveries.append(Delivery(encrypted, received))
                self.reply("250 queued")
            elif command == "QUIT":
                self.reply("221 bye")
                return
            else:
                self.reply("502 not supported")


class Login:
    def __init__(self, encrypted: bool, username: str, password: str):
        self.encrypted, self.username, self.password = encrypted, username, password


class Delivery:
    def __init__(self, encrypted: bool, message):
        self.encrypted, self.message = encrypted, message


class MailServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, tls: ssl.SSLContext | None = None):
        super().__init__(("127.0.0.1", 0), _Conversation)
        self.tls = tls
        self.port = self.server_address[1]
        self.deliveries: list[Delivery] = []
        self.logins: list[Login] = []


@contextmanager
def mail_server(certificate: tuple[str, str] | None = None):
    tls = None
    if certificate:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(*certificate)
    server = MailServer(tls)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def certificate_for(tmp_path_factory):
    """Makes a self-signed certificate for a host name; returns (certificate file, key file)."""
    if not shutil.which("openssl"):
        pytest.skip("the openssl command is needed to make a test certificate")
    folder = tmp_path_factory.mktemp("certificates")

    def make(hostname: str) -> tuple[str, str]:
        certificate, key = str(folder / f"{hostname}.pem"), str(folder / f"{hostname}.key")
        subprocess.run(  # noqa: S603 - fixed arguments
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2"]  # noqa: S607
            + ["-keyout", key, "-out", certificate, "-subj", f"/CN={hostname}"]
            + ["-addext", f"subjectAltName=DNS:{hostname}"],
            check=True,
            capture_output=True,
        )
        return certificate, key

    return make


def send(server: MailServer, message: mail.Email | None = None, **settings) -> mail.Email:
    """Sends one message through the API's own code to the test mail server."""
    settings.setdefault("smtp_username", "")
    sender = build_app(email_provider="smtp", smtp_host="localhost", smtp_port=server.port, **settings)
    with sender.app_context():
        message = message or mail.reset_email("asha@example.com", "token-123")
        mail.send_now(message)
    return message


# ---------------------------------------------------------------- the message


def test_the_message_a_mail_server_receives_is_complete(app):
    with mail_server() as server:
        sent = send(server, smtp_starttls=False, email_from="ReelForge <no-reply@reelforge.example>")

    (delivery,) = server.deliveries
    received = delivery.message
    assert received["From"] == "ReelForge <no-reply@reelforge.example>"
    assert received["To"] == "asha@example.com"
    assert received["Subject"] == sent.subject
    # Without a Date some spam filters mark the message down (QB2-012).
    sent_at = parsedate_to_datetime(received["Date"])
    if sent_at.tzinfo is None:  # written as "-0000", which means UTC
        sent_at = sent_at.replace(tzinfo=UTC)
    assert abs(sent_at - datetime.now(UTC)) < timedelta(minutes=2)
    assert re.fullmatch(r"<[^<>@\s]+@[^<>@\s]+>", received["Message-ID"])
    link = "http://localhost:5173/reset?token=token-123"
    assert link in received.get_body(("plain",)).get_content()
    assert link in received.get_body(("html",)).get_content()
    assert server.logins == [], "no user name is configured, so there is nothing to log in with"


def test_every_message_has_its_own_message_id(app):
    with mail_server() as server:
        send(server, smtp_starttls=False)
        send(server, smtp_starttls=False)

    first, second = (delivery.message["Message-ID"] for delivery in server.deliveries)
    assert first != second


# ---------------------------------------------------------------- the encrypted connection


def test_with_starttls_the_password_and_the_message_travel_encrypted(app, certificate_for, monkeypatch):
    certificate = certificate_for("localhost")
    monkeypatch.setenv("SSL_CERT_FILE", certificate[0])  # this machine trusts the test certificate
    with mail_server(certificate) as server:
        send(server, smtp_starttls=True, smtp_username="mailer", smtp_password="mail-password")

    assert [(login.encrypted, login.username, login.password) for login in server.logins] == [
        (True, "mailer", "mail-password")
    ]
    assert [delivery.encrypted for delivery in server.deliveries] == [True]


def test_a_mail_server_with_a_certificate_nobody_vouches_for_gets_nothing(app, certificate_for):
    """Anyone can answer on the mail server's address with a certificate they made themselves."""
    with mail_server(certificate_for("localhost")) as server:
        with pytest.raises(ssl.SSLCertVerificationError):
            send(server, smtp_starttls=True, smtp_username="mailer", smtp_password="mail-password")

    assert server.logins == [] and server.deliveries == []


def test_a_mail_server_showing_another_hosts_certificate_gets_nothing(app, certificate_for, monkeypatch):
    certificate = certificate_for("mail.other.example")
    monkeypatch.setenv("SSL_CERT_FILE", certificate[0])  # trusted, but not for "localhost"
    with mail_server(certificate) as server:
        with pytest.raises(ssl.SSLCertVerificationError):
            send(server, smtp_starttls=True, smtp_username="mailer", smtp_password="mail-password")

    assert server.logins == [] and server.deliveries == []


def test_nothing_is_sent_in_the_clear_when_the_server_does_not_offer_encryption(app):
    """SMTP_STARTTLS=true must not quietly fall back to an unencrypted connection."""
    with mail_server() as server:
        with pytest.raises(smtplib.SMTPException):
            send(server, smtp_starttls=True, smtp_username="mailer", smtp_password="mail-password")

    assert server.logins == [] and server.deliveries == []


# ---------------------------------------------------------------- handing emails to the worker


@pytest.fixture(scope="module")
def background_app(app):
    """The API as it runs outside the tests: emails are handed to the queue, not sent in place.
    (The `queued` fixture records what is handed over; nothing reaches the real queue.)"""
    return build_app(env="development")


def run_task(app, name: str, *args) -> None:
    """Runs a queued task the way the worker would, through Celery."""
    app.extensions["celery"].tasks[name].apply(args=list(args)).get()


@contextmanager
def sql_statements(app):
    """The SQL the app sends to the database while the block runs."""
    seen: list[str] = []

    def note(connection, cursor, statement, parameters, context, executemany):
        seen.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", note)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", note)


def test_forgot_password_does_the_same_work_whether_or_not_the_account_exists(
    app, background_app, database, outbox, queued
):
    """A7: the look-up and the sending happen in the background, so neither the answer nor
    the time it takes says whether the address has an account (QB2-004)."""
    known, unknown = sign_up(app).email, new_email()
    outbox.clear()
    answers, work = [], []

    for address in (known, unknown):
        with sql_statements(background_app) as statements:
            res = Browser(background_app).post("/auth/forgot-password", {"email": address})
        answers.append((res.status_code, res.get_data()))
        work.append(statements)

    assert answers == [(204, b""), (204, b"")]
    assert queued == [("mail.password_reset", (known,)), ("mail.password_reset", (unknown,))]
    assert work[0] == work[1], "the request did different database work for the two addresses"
    assert not any("users" in statement for statement in work[0]), "the account is looked up in the request"
    assert outbox == []
    assert database.value("SELECT count(*) FROM auth_tokens WHERE kind = 'reset_password'") == 0


def test_the_password_reset_task_emails_a_link_that_works(app, background_app, database, outbox):
    account = sign_up(app)
    outbox.clear()

    run_task(background_app, "mail.password_reset", account.email)

    (message,) = outbox
    assert message.to == account.email
    token = link_token(background_app, message, "reset")
    res = Browser(app).post("/auth/reset-password", {"token": token, "password": NEW_PASSWORD})
    assert res.status_code == 204


@pytest.mark.parametrize("who", ["nobody", "a disabled account", "a deleted account"])
def test_the_password_reset_task_sends_nothing_without_a_usable_account(
    app, background_app, database, outbox, who
):
    address = new_email()
    if who != "nobody":
        address = sign_up(app).email
        column = "disabled_at" if who == "a disabled account" else "deleted_at"
        database.run(f"UPDATE users SET {column} = now()")  # noqa: S608 - a fixed column name
    outbox.clear()

    run_task(background_app, "mail.password_reset", address)

    assert outbox == []
    assert database.value("SELECT count(*) FROM auth_tokens WHERE kind = 'reset_password'") == 0


def test_signup_hands_the_verification_email_to_the_worker(app, background_app, database, outbox, queued):
    address = new_email()
    browser = Browser(background_app)

    res = browser.post("/auth/signup", {"email": address, "password": PASSWORD, "name": "Asha"})

    assert res.status_code == 201
    assert outbox == [], "the email is sent by the worker, not while the user waits"
    ((name, (message,)),) = queued
    assert name == "mail.send"
    assert set(message) == {"to", "subject", "text", "html"} and message["to"] == address

    run_task(background_app, "mail.send", message)

    (delivered,) = outbox
    token = link_token(background_app, delivered, "verify")
    assert browser.post("/auth/verify-email", {"token": token}).get_json()["user"]["emailVerified"] is True


def test_the_request_still_succeeds_when_the_queue_cannot_be_reached(
    app, background_app, database, outbox, monkeypatch
):
    """The account action is done; the user can ask for the email again."""

    def unreachable(task, *args, **kwargs):
        raise OperationalError("the queue is down")

    monkeypatch.setattr("celery.app.task.Task.apply_async", unreachable)
    address = new_email()

    signup = Browser(background_app).post(
        "/auth/signup", {"email": address, "password": PASSWORD, "name": "Asha"}
    )
    forgot = Browser(background_app).post("/auth/forgot-password", {"email": address})

    assert (signup.status_code, forgot.status_code) == (201, 204)
    assert database.value("SELECT count(*) FROM users WHERE email = :email", email=address) == 1
