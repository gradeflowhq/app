"""The queue that hands jobs to the sandbox: each user's share of it, and what it does when the sandbox can't call
back."""

import logging
from collections.abc import Iterable, Sequence
from contextlib import suppress
from datetime import timedelta
from functools import partial
from typing import Any
from uuid import UUID

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Exists, Model, OuterRef, Q
from django.db.models.functions import Now
from django.utils import timezone

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.courses.models import Course
from gradeflow_app.jobs import events
from gradeflow_app.jobs.executors import ExecutorUnavailable, executor
from gradeflow_app.jobs.models import Job
from gradeflow_app.results.models import Result

FINISHED_JOB_LIFETIME = timedelta(days=1)
UNAVAILABLE_GRACE_PERIOD = timedelta(minutes=2)
"""How long a job handed to the executor may wait to start while the executor can't be reached."""
STOPPED_REASON = "Stopped."
"""Why a job someone stopped, or started again, ended: not a failure to show them."""
TIMED_OUT_REASON = "Ran past its time limit."
_NO_USER_LOCK_KEY = 0x4752464C4F57
_LOG = logging.getLogger(__name__)


def enqueue(
    *,
    kind: Job.Kind,
    course: Course | None,
    question_keys: Sequence[str],
    input: dict[str, Any],
    marked_with: dict[str, Any],
    user: User,
    assessment: Assessment | None = None,
    revision: int | None = None,
    **outcome_for: Model | None,
) -> Job:
    """A job running on `input`, handed to the sandbox once its user may run another. `outcome_for` names what its
    outcome is for, such as its `marking`."""
    job = Job.objects.create(
        course=course,
        assessment=assessment,
        kind=kind,
        question_keys=list(question_keys),
        created_by=user,
        input=input,
        revision=revision,
        core_version=CORE_VERSION,
        image=settings.GRADEFLOW_NOMAD["image"],
        time_limit=settings.GRADEFLOW_MODEL_JOB_TIME_LIMIT
        if input.get("model_endpoint")
        else settings.GRADEFLOW_JOB_TIME_LIMIT,
        marked_with=marked_with,
        **outcome_for,
    )
    events.notify_job_change(job)
    transaction.on_commit(partial(_dispatch_for_user, job.created_by_id))
    return job


def end(job: Job, error: str | None = None) -> None:
    """Complete the job, or fail it with `error`; either way, dispatch its user's work waiting for it to end."""
    if error is None:
        events.complete(job)
    else:
        events.fail(job, error)
    transaction.on_commit(partial(_dispatch_for_user, job.created_by_id))


def stop(job: Job | None) -> None:
    """Stop a run whose results nothing will show, such as a draft's being published or discarded."""
    if job is not None:
        cancel_jobs([job], STOPPED_REASON)


def cancel_jobs(jobs: Iterable[Job], reason: str = "Replaced by a newer run.") -> None:
    cancelled = list(jobs)
    if cancelled:
        events.fail_all(cancelled, reason)

        def cancel() -> None:
            for job in cancelled:
                executor().cancel(job)

        transaction.on_commit(cancel)


def dispatch() -> None:
    """Fail the jobs past their time limit, which frees their users' places, then dispatch queued work under each
    user's limit. Safe across concurrent workers."""
    if timed_out := list(Job.objects.timed_out()):
        _LOG.warning("Jobs ran past their time limit: %s", ", ".join(str(job.pk) for job in timed_out))
        events.fail_all(timed_out, TIMED_OUT_REASON)
    queued_users = list(
        Job.objects.filter(status=Job.Status.QUEUED, dispatched_at=None)
        .values_list("created_by_id", flat=True)
        .distinct()
    )
    for user_id in queued_users:
        _dispatch_for_user(user_id)


@transaction.atomic
def _dispatch_for_user(user_id: UUID | None) -> None:
    if user_id is not None:
        User.objects.select_for_update().get(pk=user_id)
    else:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [_NO_USER_LOCK_KEY])
    running = Job.objects.running().filter(created_by_id=user_id, dispatched_at__isnull=False).count()
    capacity = max(0, settings.GRADEFLOW_RUNNING_JOBS_PER_USER - running)
    waiting = list(
        Job.objects.select_for_update(skip_locked=True)
        .filter(created_by_id=user_id, status=Job.Status.QUEUED, dispatched_at=None)
        .order_by("created_at")
        .values_list("pk", flat=True)[:capacity]
    )
    Job.objects.filter(pk__in=waiting).update(dispatched_at=Now())
    for job_id in waiting:
        transaction.on_commit(partial(_submit, job_id))


def _submit(job_id: UUID) -> None:
    job = Job.objects.filter(pk=job_id, status__in=Job.ACTIVE).first()
    if job is None:
        return
    with suppress(ExecutorUnavailable):  # the worker hands it over again, or fails it
        executor().submit(job)
    if not Job.objects.filter(pk=job.pk, status__in=Job.ACTIVE).exists():
        job.refresh_from_db()
        if job.status == Job.Status.FAILED:
            executor().cancel(job)


def recover_queue() -> None:
    """Dispatch queued work and settle what the sandbox can't call back about. Finished jobs are forgotten after a
    while, apart from the remarks whose results are current, which record how they were marked."""
    dispatch()
    _reconcile()
    has_current_results = Exists(Result.objects.filter(marking=OuterRef("marking"), revision=OuterRef("revision")))
    Job.objects.filter(finished_at__lt=timezone.now() - FINISHED_JOB_LIFETIME).exclude(
        Q(kind=Job.Kind.REMARK, status=Job.Status.COMPLETED) & has_current_results
    ).delete()


def _reconcile() -> None:
    """Hand over again the jobs the executor never received. Fail those it ended without calling back, and those
    still waiting to start after a grace period while it can't be reached."""
    dispatched = list(
        Job.objects.filter(status__in=Job.ACTIVE, dispatched_at__isnull=False).defer("input", "result", "marked_with")
    )
    if not dispatched:
        return
    try:
        submitted = executor().submitted_jobs()
        for job in dispatched:
            if job.pk not in submitted and job.status == Job.Status.QUEUED:
                executor().submit(job)
    except ExecutorUnavailable:
        _LOG.warning("The sandbox is unavailable", exc_info=True)
        waiting = Job.objects.filter(
            status=Job.Status.QUEUED, dispatched_at__lt=timezone.now() - UNAVAILABLE_GRACE_PERIOD
        ).defer("input", "result", "marked_with")
        events.fail_all(list(waiting), "The marking service is unavailable.")
        dispatch()
        return
    # A running job the executor no longer lists has ended too.
    if ended_without_callback := [job for job in dispatched if submitted.get(job.pk, job.status == Job.Status.RUNNING)]:
        _LOG.warning(
            "The sandbox ended %s without calling back", ", ".join(str(job.pk) for job in ended_without_callback)
        )
        events.fail_all(ended_without_callback, "The marking service stopped before finishing.")
        dispatch()
