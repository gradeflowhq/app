"""Per-marking revisions, complete outputs, durable capacity and callback isolation."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.db import close_old_connections, transaction
from django.test import Client
from django.utils import timezone
from gradeflow_core.editing import dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_build import CORE_BUILD
from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import Payload
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import delete_marking, remark, schedule_marking
from gradeflow_app.results.models import Adjustment, Result
from tests.conftest import RESULTS_CSV, Waiting, marked_by_sandbox, upload

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture(autouse=True)
def waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    Waiting.submitted, Waiting.cancelled, Waiting.ended, Waiting.down = [], [], set(), False
    monkeypatch.setattr(queue, "executor", Waiting)


def single(assessment: Assessment, key: str) -> Marking:
    return Marking.objects.create(
        assessment=assessment,
        question=assessment.questions.get(key=key),
        definition=dump_yaml({"question": key, "uses": "gradeflow/full-marks@1"}),
    )


def group(assessment: Assessment, keys: list[str]) -> Marking:
    return Marking.objects.create(
        assessment=assessment,
        definition=dump_yaml(
            {
                "uses": "gradeflow/answer-sets@1",
                "with": {
                    "sets": [
                        {
                            "uses": "gradeflow/combination@1",
                            "with": {"checks": [{"question": key, "uses": "gradeflow/full-marks@1"} for key in keys]},
                        }
                    ]
                },
            }
        ),
    )


def results(job: Job) -> list[dict[str, Any]]:
    job.refresh_from_db()
    return [submission.model_dump(mode="json") for submission in marked_by_sandbox(Payload.model_validate(job.input))]


def finish(job: Job) -> None:
    events.marked(job, results(job))
    queue.finish(job)
    job.refresh_from_db()
    assert job.status == Job.Status.COMPLETED, job.error


def test_replacing_one_unit_keeps_other_jobs_and_marks(assessment: Assessment, user: User) -> None:
    q1, q2 = single(assessment, "Q1"), single(assessment, "Q2")
    remark(assessment, ["Q1", "Q2"], user)
    first, other = q1.jobs.get(), q2.jobs.get()
    late = results(first)
    replacement = schedule_marking(q1, user)
    assert replacement is not None
    first.refresh_from_db()
    other.refresh_from_db()
    assert first.status == Job.Status.FAILED
    assert other.status == Job.Status.QUEUED
    finish(other)
    before = list(Result.objects.filter(question=q2.question).values("student_id", "proportion", "revision"))
    events.marked(first, late)
    events.complete(first)
    events.fail(replacement, "Example failure")
    assert list(Result.objects.filter(question=q2.question).values("student_id", "proportion", "revision")) == before
    assert not Result.objects.filter(question=q1.question).exists()


def test_changed_read_input_regrades_complete_group_only(assessment: Assessment, user: User) -> None:
    dependent = Marking.objects.create(
        assessment=assessment,
        definition=dump_yaml(
            {
                "uses": "gradeflow/conditional@1",
                "with": {
                    "condition": [{"question": "Q2", "uses": "gradeflow/full-marks@1"}],
                    "then": [
                        {"question": "Q1", "uses": "gradeflow/full-marks@1"},
                        {"question": "Q3", "uses": "gradeflow/full-marks@1"},
                    ],
                },
            }
        ),
    )
    first = schedule_marking(dependent, user)
    assert first is not None
    finish(first)
    remark(assessment, ["Q4"], user)
    assert assessment.jobs.count() == 1
    remark(assessment, ["Q2"], user)
    job = assessment.jobs.latest("created_at")
    assert job.marking_id == dependent.pk
    assert job.questions == ["Q1", "Q3"]
    finish(job)
    assert set(Result.objects.values_list("question__key", flat=True)) == {"Q1", "Q3"}
    assert Result.objects.count() == assessment.students.count() * 2


def test_group_membership_transition_retires_old_output_before_transfer(assessment: Assessment, user: User) -> None:
    marking = group(assessment, ["Q1", "Q3"])
    first = schedule_marking(marking, user)
    assert first is not None
    late = results(first)
    finish(first)
    student = assessment.students.first()
    assert student is not None
    Adjustment.objects.create(student=student, question=assessment.questions.get(key="Q3"), marks=0, feedback="Kept")
    current = schedule_marking(marking, user)
    assert current is not None
    with transaction.atomic():
        assessment.lock()
        marking.definition = dump_yaml(
            {
                "uses": "gradeflow/conditional@1",
                "with": {
                    "condition": [{"question": "Q2", "uses": "gradeflow/full-marks@1"}],
                    "then": [{"question": "Q1", "uses": "gradeflow/full-marks@1"}],
                },
            }
        )
        marking.save()
        schedule_marking(marking, user)
    assert not Result.objects.filter(question__key="Q3").exists()
    owner = single(assessment, "Q3")
    own_job = schedule_marking(owner, user)
    assert own_job is not None
    finish(own_job)
    events.marked(current, late)
    events.complete(current)
    assert set(Result.objects.filter(question__key="Q3").values_list("marking_id", flat=True)) == {owner.pk}
    assert Adjustment.objects.get().feedback == "Kept"


def test_queue_wait_does_not_timeout_and_every_unit_eventually_runs(
    assessment: Assessment, user: User, settings: Settings
) -> None:
    settings.GRADEFLOW_JOBS_PER_USER = 1
    markings = [single(assessment, key) for key in ["Q1", "Q2", "Q3"]]
    remark(assessment, ["Q1", "Q2", "Q3"], user)
    assert len(Waiting.submitted) == 1
    waiting = Job.objects.filter(admitted_at=None)
    waiting.update(created_at=timezone.now() - timedelta(days=2))
    assert not Job.objects.lost().exists()
    first = Job.objects.get(pk=Waiting.submitted[0])
    queue.finish(first, "Retry needed")
    for _ in range(2):
        active = Job.objects.get(status__in=Job.ACTIVE, admitted_at__isnull=False)
        finish(active)
    retry = schedule_marking(markings[0], user)
    assert retry is not None
    finish(retry)
    assert not Job.objects.filter(status__in=Job.ACTIVE).exists()
    assert Result.objects.count() == assessment.students.count() * 3


def test_worker_hands_over_again_what_the_sandbox_never_received(assessment: Assessment, user: User) -> None:
    job = schedule_marking(single(assessment, "Q1"), user)
    assert job is not None
    queue.recover_queue()
    assert Waiting.submitted == [job.pk], "a received job isn't handed over again"
    Waiting.submitted.clear()
    queue.recover_queue()
    assert Waiting.submitted == [job.pk]


def test_a_job_started_while_the_sandbox_is_down_waits_for_the_worker_to_fail_it(
    assessment: Assessment, user: User
) -> None:
    Waiting.down = True
    job = schedule_marking(single(assessment, "Q1"), user)
    assert job is not None
    job.refresh_from_db()
    assert job.status == Job.Status.QUEUED, "the page that started it shows what its events change next"
    queue.recover_queue()
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, "The marking service is unavailable.")


def test_running_jobs_outlast_a_sandbox_outage(assessment: Assessment, user: User) -> None:
    job = schedule_marking(single(assessment, "Q1"), user)
    assert job is not None
    events.started(job)
    Waiting.down = True
    queue.recover_queue()
    job.refresh_from_db()
    assert job.status == Job.Status.RUNNING


@pytest.mark.parametrize("started", [False, True])
def test_a_job_the_sandbox_ended_without_calling_back_fails(assessment: Assessment, user: User, started: bool) -> None:
    job = schedule_marking(single(assessment, "Q1"), user)
    assert job is not None
    if started:
        events.started(job)
    Waiting.ended.add(job.pk)
    queue.recover_queue()
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, "The marking service stopped before finishing.")


def test_a_running_job_the_sandbox_no_longer_lists_fails(assessment: Assessment, user: User) -> None:
    job = schedule_marking(single(assessment, "Q1"), user)
    assert job is not None
    events.started(job)
    Waiting.submitted.clear()
    queue.recover_queue()
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED
    assert not Waiting.submitted, "a started job isn't run twice"


def test_a_job_keeps_what_marked_after_its_input_is_cleared(assessment: Assessment, user: User) -> None:
    marking = single(assessment, "Q1")
    job = schedule_marking(marking, user)
    assert job is not None
    finish(job)
    job.refresh_from_db()
    result = Result.objects.first()
    assert result is not None
    assert job.input is None
    assert (job.core_build, job.image) == (CORE_BUILD, settings.GRADEFLOW_NOMAD["image"])
    assert job.provenance == {"markings": [{"question": "Q1", "uses": "gradeflow/full-marks@1"}], "methods": []}
    assert (result.marking_id, result.revision) == (marking.pk, job.revision)


def test_complete_and_edit_serialize_on_real_connections(assessment: Assessment, user: User) -> None:
    marking = single(assessment, "Q1")
    job = schedule_marking(marking, user)
    assert job is not None
    events.marked(job, results(job))
    together = threading.Barrier(2)

    def run(index: int) -> None:
        close_old_connections()
        try:
            together.wait(timeout=10)
            if index:
                schedule_marking(Marking.objects.get(pk=marking.pk), user)
            else:
                events.complete(Job.objects.get(pk=job.pk))
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, range(2)))
    marking.refresh_from_db()
    latest = marking.jobs.get(status__in=Job.ACTIVE)
    assert marking.revision == latest.revision == 2
    finish(latest)
    assert set(Result.objects.values_list("revision", flat=True)) == {2}


def test_reimport_only_supersedes_units_reading_changed_answers(
    client: Client, assessment: Assessment, user: User
) -> None:
    q1, q2 = single(assessment, "Q1"), single(assessment, "Q2")
    remark(assessment, ["Q1", "Q2"], user)
    first, untouched = q1.jobs.get(), q2.jobs.get()
    upload(client, assessment)
    assert q1.jobs.count() == q2.jobs.count() == 1
    upload(client, assessment, RESULTS_CSV.replace("photosynthesis uses light", "changed answer"))
    first.refresh_from_db()
    untouched.refresh_from_db()
    assert first.status == Job.Status.FAILED
    assert untouched.status == Job.Status.QUEUED
    assert q2.jobs.count() == 1


def test_deleted_owner_and_duplicate_callbacks_cannot_change_marks(assessment: Assessment, user: User) -> None:
    marking = single(assessment, "Q1")
    job = schedule_marking(marking, user)
    assert job is not None
    sent = results(job)
    events.marked(job, sent)
    events.marked(job, sent)
    assert job.batches.count() == 1
    events.complete(job)
    before = list(Result.objects.values("student_id", "proportion", "revision"))
    events.marked(job, sent)
    events.complete(job)
    events.fail(job, "Late failure")
    assert list(Result.objects.values("student_id", "proportion", "revision")) == before
    active = schedule_marking(marking, user)
    assert active is not None
    delete_marking(marking)
    events.marked(active, sent)
    events.complete(active)
    assert not Result.objects.exists()


def test_incomplete_result_fails_without_replacing_existing_marks(assessment: Assessment, user: User) -> None:
    marking = single(assessment, "Q1")
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    newer = schedule_marking(marking, user)
    assert newer is not None
    events.marked(newer, results(newer)[:1])
    events.complete(newer)
    newer.refresh_from_db()
    assert newer.status == Job.Status.FAILED
    assert "incomplete" in newer.error
    assert set(Result.objects.values_list("revision", flat=True)) == {first.revision}


def test_an_invalid_marking_replaces_its_run_without_touching_its_marks(assessment: Assessment, user: User) -> None:
    marking = Marking.objects.create(
        assessment=assessment,
        definition=dump_yaml(
            {
                "uses": "gradeflow/conditional@1",
                "with": {
                    "condition": [{"question": "Q2", "uses": "gradeflow/full-marks@1"}],
                    "then": [{"question": "Q1", "uses": "gradeflow/full-marks@1"}],
                },
            }
        ),
    )
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    marking.refresh_from_db()
    assert marking.input_keys == ["Q1", "Q2"]
    assert marking.output_keys == ["Q1"]
    running = schedule_marking(marking, user)
    assert running is not None
    untouched = schedule_marking(single(assessment, "Q3"), user)
    assert untouched is not None
    previous = list(Result.objects.values("student_id", "proportion", "revision"))
    marking.definition = "uses: course-unavailable@1"
    marking.save()
    submitted = list(Waiting.submitted)
    jobs = assessment.jobs.count()
    remark(assessment, ["Q2"], user)
    assert assessment.jobs.count() == jobs
    running.refresh_from_db()
    assert (running.status, running.error) == (Job.Status.FAILED, "Replaced by a newer run.")
    assert list(Result.objects.values("student_id", "proportion", "revision")) == previous
    assert Waiting.submitted == submitted
    marking.refresh_from_db()
    assert running.revision is not None
    assert marking.revision == running.revision + 1
    assert marking.output_keys == ["Q1"], "it still owns its questions"
    untouched.refresh_from_db()
    assert untouched.status == Job.Status.QUEUED


def test_incompatible_question_settings_retire_running_revision_and_keep_previous_marks(
    assessment: Assessment, user: User
) -> None:
    question = assessment.questions.get(key="Q1")
    marking = Marking.objects.create(
        assessment=assessment,
        question=question,
        definition=dump_yaml({"question": "Q1", "uses": "gradeflow/key-point@1", "with": {"point": "light"}}),
    )
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    previous = list(Result.objects.values("student_id", "proportion", "revision"))
    running = schedule_marking(marking, user)
    assert running is not None
    sent = results(running)
    with transaction.atomic():
        assessment.lock()
        question.definition = {"type": "number", "max_marks": 1}
        question.save()
        jobs = assessment.jobs.count()
        remark(assessment, ["Q1"], user)
        assert assessment.jobs.count() == jobs
    running.refresh_from_db()
    assert running.status == Job.Status.FAILED
    events.marked(running, sent)
    events.complete(running)
    assert list(Result.objects.values("student_id", "proportion", "revision")) == previous


def test_an_invalid_saved_definition_keeps_the_marks_of_every_question_it_marked(
    assessment: Assessment, user: User
) -> None:
    marking = group(assessment, ["Q1", "Q3"])
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    marking.refresh_from_db()
    marking.definition = "uses: course-unavailable@1"
    marking.save()
    jobs = assessment.jobs.count()
    remark(assessment, ["Q3"], user)
    assert assessment.jobs.count() == jobs
    marking.refresh_from_db()
    assert marking.output_keys == ["Q1", "Q3"]
    assert Result.objects.count() == assessment.students.count() * 2


def test_concurrent_system_dispatch_shares_one_capacity_limit(assessment: Assessment, settings: Settings) -> None:
    settings.GRADEFLOW_JOBS_PER_USER = 1
    with transaction.atomic():
        for key in ["Q1", "Q2", "Q3"]:
            schedule_marking(single(assessment, key))
    admitted = Job.objects.get(admitted_at__isnull=False)
    Job.objects.filter(pk=admitted.pk).update(status=Job.Status.COMPLETED)
    together = threading.Barrier(2)

    def dispatch() -> None:
        close_old_connections()
        try:
            together.wait(timeout=10)
            queue.dispatch()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(dispatch) for _ in range(2)]
        for future in futures:
            future.result(timeout=10)
    assert Job.objects.filter(status__in=Job.ACTIVE, admitted_at__isnull=False).count() == 1
    assert Job.objects.filter(status=Job.Status.QUEUED, admitted_at=None).count() == 1
