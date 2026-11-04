"""Each marking's jobs: revisions, complete results, the per-user job limit, and callbacks that can't change another
job's marks."""

from datetime import timedelta
from typing import Any

import pytest
from django.conf import settings
from django.db import transaction
from django.test import Client
from django.utils import timezone
from gradeflow_core.editing import dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import delete_marking, remark, schedule_marking
from gradeflow_app.results.models import Adjustment, Result
from tests.conftest import EXPORT_CSV, FakeNomad, full_marks_marking, marked_by_sandbox, run_together, upload

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("nomad")]


def question_group_marking(assessment: Assessment, keys: list[str]) -> Marking:
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


def marked_submissions(job: Job) -> list[dict[str, Any]]:
    job.refresh_from_db()
    return [
        submission.model_dump(mode="json") for submission in marked_by_sandbox(MarkingRun.model_validate(job.input))
    ]


def finish(job: Job) -> None:
    events.add_marked_batch(job, marked_submissions(job))
    queue.end(job)
    job.refresh_from_db()
    assert job.status == Job.Status.COMPLETED, job.error


def test_replacing_one_markings_job_keeps_other_jobs_and_marks(assessment: Assessment, user: User) -> None:
    q1_marking, q2_marking = full_marks_marking(assessment, "Q1"), full_marks_marking(assessment, "Q2")
    remark(assessment, ["Q1", "Q2"], user)
    first, other = q1_marking.jobs.get(), q2_marking.jobs.get()
    late_submissions = marked_submissions(first)
    replacement = schedule_marking(q1_marking, user)
    assert replacement is not None
    first.refresh_from_db()
    other.refresh_from_db()
    assert first.status == Job.Status.FAILED
    assert other.status == Job.Status.QUEUED
    finish(other)
    before = list(Result.objects.filter(question=q2_marking.question).values("student_id", "proportion", "revision"))
    events.add_marked_batch(first, late_submissions)
    events.complete(first)
    events.fail(replacement, "Example failure")
    assert (
        list(Result.objects.filter(question=q2_marking.question).values("student_id", "proportion", "revision"))
        == before
    )
    assert not Result.objects.filter(question=q1_marking.question).exists()


def test_a_changed_question_a_marking_reads_remarks_that_marking_only(assessment: Assessment, user: User) -> None:
    conditional_marking = Marking.objects.create(
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
    first = schedule_marking(conditional_marking, user)
    assert first is not None
    finish(first)
    remark(assessment, ["Q4"], user)
    assert assessment.jobs.count() == 1
    remark(assessment, ["Q2"], user)
    job = assessment.jobs.latest("created_at")
    assert job.marking_id == conditional_marking.pk
    assert job.question_keys == ["Q1", "Q3"]
    finish(job)
    assert set(Result.objects.values_list("question__key", flat=True)) == {"Q1", "Q3"}
    assert Result.objects.count() == assessment.students.count() * 2


def test_a_question_leaving_a_group_loses_its_old_marks_before_another_marking_takes_it(
    assessment: Assessment, user: User
) -> None:
    marking = question_group_marking(assessment, ["Q1", "Q3"])
    first = schedule_marking(marking, user)
    assert first is not None
    late_submissions = marked_submissions(first)
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
    q3_marking = full_marks_marking(assessment, "Q3")
    q3_job = schedule_marking(q3_marking, user)
    assert q3_job is not None
    finish(q3_job)
    events.add_marked_batch(current, late_submissions)
    events.complete(current)
    assert set(Result.objects.filter(question__key="Q3").values_list("marking_id", flat=True)) == {q3_marking.pk}
    assert Adjustment.objects.get().feedback == "Kept"


def test_queue_wait_does_not_timeout_and_every_marking_eventually_runs(
    assessment: Assessment, user: User, nomad: FakeNomad, settings: Settings
) -> None:
    settings.GRADEFLOW_RUNNING_JOBS_PER_USER = 1
    markings = [full_marks_marking(assessment, key) for key in ["Q1", "Q2", "Q3"]]
    remark(assessment, ["Q1", "Q2", "Q3"], user)
    assert len(nomad.submitted) == 1
    undispatched_jobs = Job.objects.filter(dispatched_at=None)
    undispatched_jobs.update(created_at=timezone.now() - timedelta(days=2))
    assert not Job.objects.timed_out().exists()
    first = Job.objects.get(pk=nomad.submitted[0])
    queue.end(first, "Retry needed")
    for _ in range(2):
        active_job = Job.objects.get(status__in=Job.ACTIVE, dispatched_at__isnull=False)
        finish(active_job)
    retry = schedule_marking(markings[0], user)
    assert retry is not None
    finish(retry)
    assert not Job.objects.filter(status__in=Job.ACTIVE).exists()
    assert Result.objects.count() == assessment.students.count() * 3


def test_worker_hands_over_again_what_the_sandbox_never_received(
    assessment: Assessment, user: User, nomad: FakeNomad
) -> None:
    job = schedule_marking(full_marks_marking(assessment, "Q1"), user)
    assert job is not None
    queue.recover_queue()
    assert nomad.submitted == [job.pk], "a received job isn't handed over again"
    nomad.submitted.clear()
    queue.recover_queue()
    assert nomad.submitted == [job.pk]


def test_a_job_started_while_the_sandbox_is_down_waits_for_the_worker_to_fail_it(
    assessment: Assessment, user: User, nomad: FakeNomad
) -> None:
    nomad.is_down = True
    job = schedule_marking(full_marks_marking(assessment, "Q1"), user)
    assert job is not None
    job.refresh_from_db()
    assert job.status == Job.Status.QUEUED, "the page that started it shows what its events change next"
    queue.recover_queue()
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, "The marking service is unavailable.")


def test_running_jobs_outlast_a_sandbox_outage(assessment: Assessment, user: User, nomad: FakeNomad) -> None:
    job = schedule_marking(full_marks_marking(assessment, "Q1"), user)
    assert job is not None
    events.apply_started(job)
    nomad.is_down = True
    queue.recover_queue()
    job.refresh_from_db()
    assert job.status == Job.Status.RUNNING


@pytest.mark.parametrize("was_started", [False, True])
def test_a_job_the_sandbox_ended_without_calling_back_fails(
    assessment: Assessment, user: User, nomad: FakeNomad, was_started: bool
) -> None:
    job = schedule_marking(full_marks_marking(assessment, "Q1"), user)
    assert job is not None
    if was_started:
        events.apply_started(job)
    nomad.ended.add(job.pk)
    queue.recover_queue()
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, "The marking service stopped before finishing.")


def test_a_running_job_the_sandbox_no_longer_lists_fails(assessment: Assessment, user: User, nomad: FakeNomad) -> None:
    job = schedule_marking(full_marks_marking(assessment, "Q1"), user)
    assert job is not None
    events.apply_started(job)
    nomad.submitted.clear()
    queue.recover_queue()
    job.refresh_from_db()
    assert job.status == Job.Status.FAILED
    assert not nomad.submitted, "a started job isn't run twice"


def test_a_job_keeps_what_marked_after_its_input_is_cleared(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    job = schedule_marking(marking, user)
    assert job is not None
    finish(job)
    job.refresh_from_db()
    result = Result.objects.first()
    assert result is not None
    assert job.input is None
    assert (job.core_version, job.image) == (CORE_VERSION, settings.GRADEFLOW_NOMAD["image"])
    assert job.marked_with == {"markings": [{"question": "Q1", "uses": "gradeflow/full-marks@1"}], "methods": []}
    assert (result.marking_id, result.revision) == (marking.pk, job.revision)


def test_complete_and_edit_serialize_on_real_connections(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    job = schedule_marking(marking, user)
    assert job is not None
    events.add_marked_batch(job, marked_submissions(job))
    run_together(
        lambda: events.complete(Job.objects.get(pk=job.pk)),
        lambda: schedule_marking(Marking.objects.get(pk=marking.pk), user),
    )
    marking.refresh_from_db()
    latest = marking.jobs.get(status__in=Job.ACTIVE)
    assert marking.revision == latest.revision == 2
    finish(latest)
    assert set(Result.objects.values_list("revision", flat=True)) == {2}


def test_reimport_only_supersedes_the_jobs_of_markings_reading_changed_answers(
    client: Client, assessment: Assessment, user: User
) -> None:
    q1_marking, q2_marking = full_marks_marking(assessment, "Q1"), full_marks_marking(assessment, "Q2")
    remark(assessment, ["Q1", "Q2"], user)
    first, untouched = q1_marking.jobs.get(), q2_marking.jobs.get()
    upload(client, assessment)
    assert q1_marking.jobs.count() == q2_marking.jobs.count() == 1
    upload(client, assessment, EXPORT_CSV.replace("photosynthesis uses light", "changed answer"))
    first.refresh_from_db()
    untouched.refresh_from_db()
    assert first.status == Job.Status.FAILED
    assert untouched.status == Job.Status.QUEUED
    assert q2_marking.jobs.count() == 1


def test_deleted_owner_and_duplicate_callbacks_cannot_change_marks(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    job = schedule_marking(marking, user)
    assert job is not None
    submissions = marked_submissions(job)
    events.add_marked_batch(job, submissions)
    events.add_marked_batch(job, submissions)
    assert job.batches.count() == 1
    events.complete(job)
    before = list(Result.objects.values("student_id", "proportion", "revision"))
    events.add_marked_batch(job, submissions)
    events.complete(job)
    events.fail(job, "Late failure")
    assert list(Result.objects.values("student_id", "proportion", "revision")) == before
    active_job = schedule_marking(marking, user)
    assert active_job is not None
    delete_marking(marking)
    events.add_marked_batch(active_job, submissions)
    events.complete(active_job)
    assert not Result.objects.exists()


def test_incomplete_result_fails_without_replacing_existing_marks(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    newer = schedule_marking(marking, user)
    assert newer is not None
    events.add_marked_batch(newer, marked_submissions(newer)[:1])
    events.complete(newer)
    newer.refresh_from_db()
    assert newer.status == Job.Status.FAILED
    assert "incomplete" in newer.error
    assert set(Result.objects.values_list("revision", flat=True)) == {first.revision}


def test_an_invalid_marking_replaces_its_run_without_touching_its_marks(
    assessment: Assessment, user: User, nomad: FakeNomad
) -> None:
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
    assert marking.questions_read == ["Q1", "Q2"]
    assert marking.questions_marked == ["Q1"]
    running = schedule_marking(marking, user)
    assert running is not None
    untouched = schedule_marking(full_marks_marking(assessment, "Q3"), user)
    assert untouched is not None
    previous = list(Result.objects.values("student_id", "proportion", "revision"))
    marking.definition = "uses: course-unavailable@1"
    marking.save()
    submitted = list(nomad.submitted)
    job_count = assessment.jobs.count()
    remark(assessment, ["Q2"], user)
    assert assessment.jobs.count() == job_count
    running.refresh_from_db()
    assert (running.status, running.error) == (Job.Status.FAILED, "Replaced by a newer run.")
    assert list(Result.objects.values("student_id", "proportion", "revision")) == previous
    assert nomad.submitted == submitted
    marking.refresh_from_db()
    assert running.revision is not None
    assert marking.revision == running.revision + 1
    assert marking.questions_marked == ["Q1"], "it still owns its questions"
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
    submissions = marked_submissions(running)
    with transaction.atomic():
        assessment.lock()
        question.definition = {"type": "number", "max_marks": 1}
        question.save()
        job_count = assessment.jobs.count()
        remark(assessment, ["Q1"], user)
        assert assessment.jobs.count() == job_count
    running.refresh_from_db()
    assert running.status == Job.Status.FAILED
    events.add_marked_batch(running, submissions)
    events.complete(running)
    assert list(Result.objects.values("student_id", "proportion", "revision")) == previous


def test_an_invalid_saved_definition_keeps_the_marks_of_every_question_it_marked(
    assessment: Assessment, user: User
) -> None:
    marking = question_group_marking(assessment, ["Q1", "Q3"])
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    marking.refresh_from_db()
    marking.definition = "uses: course-unavailable@1"
    marking.save()
    job_count = assessment.jobs.count()
    remark(assessment, ["Q3"], user)
    assert assessment.jobs.count() == job_count
    marking.refresh_from_db()
    assert marking.questions_marked == ["Q1", "Q3"]
    assert Result.objects.count() == assessment.students.count() * 2


def test_dispatching_twice_at_once_still_dispatches_one_job_per_user(
    assessment: Assessment, user: User, settings: Settings
) -> None:
    settings.GRADEFLOW_RUNNING_JOBS_PER_USER = 1
    with transaction.atomic():
        for key in ["Q1", "Q2", "Q3"]:
            schedule_marking(full_marks_marking(assessment, key), user)
    Job.objects.update(created_by=None)  # as after the user is deleted
    dispatched = Job.objects.get(dispatched_at__isnull=False)
    Job.objects.filter(pk=dispatched.pk).update(status=Job.Status.COMPLETED)
    run_together(queue.dispatch, queue.dispatch)
    assert Job.objects.filter(status__in=Job.ACTIVE, dispatched_at__isnull=False).count() == 1
    assert Job.objects.filter(status=Job.Status.QUEUED, dispatched_at=None).count() == 1
