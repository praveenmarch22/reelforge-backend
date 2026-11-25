from celery import shared_task

from .. import credits, jobs


@shared_task(
    name="jobs.run",
    acks_late=True,
    ignore_result=True,
    # Language-model jobs (queue llm). Time limits for other queues come with their tasks.
    soft_time_limit=240,
    time_limit=300,
)
def run_job(job_id: str) -> None:
    """Runs one job (jobs.run). A second delivery of the same task finds the job no longer
    queued and does nothing."""
    jobs.run(job_id)


@shared_task(name="jobs.fail_stuck", ignore_result=True)
def fail_stuck_jobs() -> int:
    """Every 5 minutes: jobs past their time limit fail and are refunded (G-11)."""
    return jobs.fail_stuck()


@shared_task(name="credits.reset_monthly", ignore_result=True)
def reset_monthly_credits() -> int:
    """Hourly: accounts whose reset date has passed get their allowance again (CR-4)."""
    return credits.reset_due_accounts()
