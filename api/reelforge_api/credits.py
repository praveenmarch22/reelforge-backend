"""Credits: reserve when a job is created, refund when it fails or is cancelled (technical design 8).

The balance in credit_accounts only ever changes together with a credit_ledger row, in the
same transaction. Reserving locks the account row, so two requests at the same moment can
never spend the same credits (CR-3).
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select, text, update

from .accounts import next_month_start
from .errors import ApiError
from .extensions import db
from .models import CreditAccount, CreditLedger, Job
from .sessions import now

log = logging.getLogger(__name__)

NOTES = {
    "script": "Script",
    "rewrite_scene": "Scene rewrite",
    "voice": "Voiceover",
    "visual": "Visual",
    "keyframe": "AI image",
    "ai_video": "AI video clip",
    "render": "Render",
    "transcribe": "Captions from audio",
}


def lock_account(user_id: uuid.UUID) -> CreditAccount:
    return db.session.execute(
        select(CreditAccount)
        .where(CreditAccount.user_id == user_id)
        .with_for_update()
        # The account may already be loaded with the user (an older copy); FOR UPDATE must
        # give the row as it is now, after waiting for the lock (QB3-001).
        .execution_options(populate_existing=True)
    ).scalar_one()


def insufficient(needed: int, balance: int, resets_at) -> ApiError:
    return ApiError(
        402,
        "insufficient_credits",
        f"This needs {needed} credits; you have {balance}.",
        {
            "needed": needed,
            "balance": balance,
            "resetsAt": resets_at.strftime("%Y-%m-%dT%H:%M:%SZ") if resets_at else None,
        },
    )


def reserve(account: CreditAccount, jobs: list[Job]) -> None:
    """Reserves the credits for these new jobs, all or nothing (CR-1). The account must be
    locked (lock_account) and the jobs flushed (they need ids). Caller commits."""
    total = sum(job.credits_reserved for job in jobs)
    if total > account.balance:
        raise insufficient(total, account.balance, account.resets_at)
    for job in jobs:
        if job.credits_reserved:
            db.session.add(
                CreditLedger(
                    user_id=account.user_id,
                    amount=-job.credits_reserved,
                    kind="reserve",
                    job_id=job.id,
                    note=NOTES.get(job.type, job.type),
                )
            )
    # Written as balance - total in SQL, never as a number computed in Python.
    db.session.execute(
        update(CreditAccount)
        .where(CreditAccount.user_id == account.user_id)
        .values(balance=CreditAccount.balance - total)
    )
    db.session.refresh(account)


def refund(job: Job) -> int:
    """Gives back a job's reserved credits. Safe to call more than once: the ledger allows
    one refund per job (DB design 6.2). Returns the credits given back. Caller commits."""
    if job.credits_reserved <= 0:
        return 0
    inserted = db.session.execute(
        text(
            "INSERT INTO credit_ledger (user_id, amount, kind, job_id, note) "
            "VALUES (:user_id, :amount, 'refund', :job_id, :note) ON CONFLICT DO NOTHING RETURNING amount"
        ),
        {
            "user_id": job.user_id,
            "amount": job.credits_reserved,
            "job_id": job.id,
            "note": f"Refund: {NOTES.get(job.type, job.type)}",
        },
    ).scalar()
    if not inserted:
        return 0
    db.session.execute(
        update(CreditAccount)
        .where(CreditAccount.user_id == job.user_id)
        .values(balance=CreditAccount.balance + inserted)
    )
    return inserted


def was_refunded(job_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not job_ids:
        return set()
    rows = db.session.execute(
        select(CreditLedger.job_id).where(CreditLedger.job_id.in_(job_ids), CreditLedger.kind == "refund")
    )
    return {row[0] for row in rows}


def reset_due_accounts(limit: int = 500) -> int:
    """Monthly reset (CR-4): unused credits don't carry over; the allowance is granted again.

    Credits still reserved by running jobs are not touched: if such a job fails later, its
    refund is added on top. Each account is reset in its own short transaction.
    """
    due = list(
        db.session.execute(
            select(CreditAccount.user_id).where(CreditAccount.resets_at <= now()).limit(limit)
        ).scalars()
    )
    for user_id in due:
        account = db.session.execute(
            select(CreditAccount)
            .where(CreditAccount.user_id == user_id, CreditAccount.resets_at <= now())
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        ).scalar_one_or_none()
        if account is None:  # reset by another worker in the meantime
            db.session.rollback()
            continue
        if account.balance:
            db.session.add(
                CreditLedger(user_id=user_id, amount=-account.balance, kind="adjust", note="Monthly reset")
            )
        if account.monthly_allowance:
            db.session.add(
                CreditLedger(
                    user_id=user_id, amount=account.monthly_allowance, kind="grant", note="Monthly allowance"
                )
            )
        account.balance = account.monthly_allowance
        # From the reset date, not from today, so a late run doesn't shift the cycle.
        account.resets_at = next_month_start(max(account.resets_at, now()))
        db.session.commit()
    if due:
        log.info("monthly credits reset", extra={"count": len(due)})
    return len(due)
