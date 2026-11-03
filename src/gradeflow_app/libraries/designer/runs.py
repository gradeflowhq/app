"""Runs of a method's test answers: a job marks them, for the draft or the version's tests that asked."""

from collections.abc import Callable
from uuid import UUID

from django.db import transaction

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import TestRun
from gradeflow_app.libraries.models import MethodDraftRecord, VersionTests
from gradeflow_app.live.topics import topic


@transaction.atomic
def run_tests(tests: MethodDraftRecord | VersionTests, test_run: TestRun, course: Course | None, user: User) -> Job:
    """Mark a method's test answers, replacing the earlier run of `tests`."""
    tests = type(tests).objects.select_for_update().get(pk=tests.pk)
    if tests.last_run is not None:
        queue.cancel_jobs([tests.last_run])
    is_draft = isinstance(tests, MethodDraftRecord)
    job = queue.enqueue(
        course=course,
        kind=Job.Kind.DRAFT_TESTS if is_draft else Job.Kind.VERSION_TESTS,
        question_keys=[],
        input=test_run.model_dump(mode="json"),
        marked_with={"method": test_run.method, "methods": test_run.methods},
        user=user,
        revision=tests.revision,
        method_draft=tests if is_draft else None,
        version_tests=None if is_draft else tests,
    )
    tests.last_run = job
    tests.save(update_fields=["last_run"])
    return job


def _handler(
    model: type[MethodDraftRecord | VersionTests], tests_of: Callable[[Job], UUID | None], about: str
) -> Handler[None]:
    """Jobs of `model`'s runs: `tests_of` is the ID of the tests a job ran, `about` the topic their page listens to."""

    def lock(job: Job) -> None:
        if (pk := tests_of(job)) is not None:
            model.objects.select_for_update().filter(pk=pk).first()

    def is_current(job: Job) -> bool:
        pk = tests_of(job)
        return (
            pk is not None
            and job.revision is not None
            and model.objects.filter(pk=pk, last_run=job, revision=job.revision).exists()
        )

    return Handler(task="test", topics=lambda job: [topic(about, tests_of(job))], lock=lock, is_current=is_current)


register(Job.Kind.DRAFT_TESTS, _handler(MethodDraftRecord, lambda job: job.method_draft_id, "method-draft"))
register(Job.Kind.VERSION_TESTS, _handler(VersionTests, lambda job: job.version_tests_id, "version-tests"))
