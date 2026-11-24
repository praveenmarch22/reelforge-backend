from celery import shared_task

from ..mail import Email, send_now, send_password_reset


@shared_task(
    name="mail.send",
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_backoff_max=300,
    max_retries=5,
    ignore_result=True,
)
def send_email(message: dict) -> None:
    """Sends one email; retried with growing pauses if the mail service is unavailable."""
    send_now(Email(**message))


@shared_task(
    name="mail.password_reset",
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_backoff_max=300,
    max_retries=5,
    ignore_result=True,
)
def password_reset(address: str) -> None:
    """Looks the account up and emails a reset link. A retry issues a fresh link."""
    send_password_reset(address)
