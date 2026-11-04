"""Runs of a method's test answers: a job marks them, for the draft or the version's tests that asked."""

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
    )
    tests.last_run = job
    tests.save(update_fields=["last_run"])
    return job


def tests_topic(tests: MethodDraftRecord | VersionTests) -> str:
    """What the page showing the runs of `tests` listens to, with their ID."""
    return "method-draft" if isinstance(tests, MethodDraftRecord) else "version-tests"


def _handler(model: type[MethodDraftRecord | VersionTests]) -> Handler:
    """Jobs of `model`'s runs: each is the last run of the tests it ran, until replaced."""

    def lock(job: Job) -> None:
        model.objects.select_for_update().filter(last_run=job).first()

    def is_current(job: Job) -> bool:
        return job.revision is not None and model.objects.filter(last_run=job, revision=job.revision).exists()

    def topics(job: Job) -> list[str]:
        return [topic(tests_topic(tests), tests.pk) for tests in model.objects.filter(last_run=job)]

    return Handler(task="test", topics=topics, lock=lock, is_current=is_current)


register(Job.Kind.DRAFT_TESTS, _handler(MethodDraftRecord))
register(Job.Kind.VERSION_TESTS, _handler(VersionTests))
