"""Each marking's runs: revisions, complete results, and callbacks that can't change another run's marks."""

from collections.abc import Mapping
from typing import Any

import pytest
from django.db import transaction
from django.test import Client
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import handler
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.jobs.runner import Run
from gradeflow_app.marking.models import Marking
from gradeflow_app.marking.remarks import REMARK, delete_marking, remark, schedule_marking
from gradeflow_app.results.models import Adjustment, Result
from tests.conftest import (
    EXAM_CSV,
    FakeRunner,
    full_marks_marking,
    marked_by_sandbox,
    post_callback,
    run_together,
    upload,
)

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.usefixtures("idle_runner")]


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


def active_run(marking: Marking) -> Run:
    [(run, _)] = runs.active(REMARK, marking.pk)
    return run


def marked_submissions(run: Run) -> list[dict[str, Any]]:
    run_input = handler(run).input(run)
    assert isinstance(run_input, MarkingRun)
    return [submission.model_dump(mode="json") for submission in marked_by_sandbox(run_input)]


def send_finished(run: Run, submissions: list[dict[str, Any]]) -> None:
    assert post_callback(run.id, {"event": "finished", "outcome": submissions}) == 204


def finish(run: Run) -> None:
    send_finished(run, marked_submissions(run))
    assert Marking.objects.get(pk=run.owner).failure == ""


def results() -> list[Mapping[str, object]]:
    return list(Result.objects.values("student_id", "proportion", "revision"))


def test_replacing_one_markings_run_keeps_other_runs_and_marks(
    assessment: Assessment, user: User, idle_runner: FakeRunner
) -> None:
    q1_marking, q2_marking = full_marks_marking(assessment, "Q1"), full_marks_marking(assessment, "Q2")
    remark(assessment, ["Q1", "Q2"], user)
    first, other = active_run(q1_marking), active_run(q2_marking)
    late_submissions = marked_submissions(first)
    replacement = schedule_marking(q1_marking, user)
    assert replacement is not None
    assert first.id in idle_runner.stopped
    assert other.id in idle_runner.held
    finish(other)
    before = list(Result.objects.filter(question=q2_marking.question).values("student_id", "proportion", "revision"))
    send_finished(first, late_submissions)
    post_callback(replacement.id, {"event": "failed", "error": "Example failure"})
    assert (
        list(Result.objects.filter(question=q2_marking.question).values("student_id", "proportion", "revision"))
        == before
    )
    assert not Result.objects.filter(question=q1_marking.question).exists()


def test_a_changed_question_a_marking_reads_remarks_that_marking_only(
    assessment: Assessment, user: User, idle_runner: FakeRunner
) -> None:
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
    assert len(idle_runner.run_ids(REMARK)) == 1
    remark(assessment, ["Q2"], user)
    run = active_run(conditional_marking)
    conditional_marking.refresh_from_db()
    assert conditional_marking.questions_marked == ["Q1", "Q3"]
    finish(run)
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
    q3_run = schedule_marking(q3_marking, user)
    assert q3_run is not None
    finish(q3_run)
    send_finished(current, late_submissions)
    assert set(Result.objects.filter(question__key="Q3").values_list("marking_id", flat=True)) == {q3_marking.pk}
    assert Adjustment.objects.get().feedback == "Kept"


def test_results_name_the_marking_and_revision_that_gave_them(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    run = schedule_marking(marking, user)
    assert run is not None
    finish(run)
    result = Result.objects.first()
    assert result is not None
    assert (result.marking_id, str(result.revision)) == (marking.pk, run.key)


def test_finishing_and_editing_serialize_on_real_connections(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    run = schedule_marking(marking, user)
    assert run is not None
    submissions = marked_submissions(run)
    run_together(
        lambda: runs.end(run, outcome=submissions),
        lambda: schedule_marking(Marking.objects.get(pk=marking.pk), user),
    )
    marking.refresh_from_db()
    latest = active_run(marking)
    assert marking.revision == int(latest.key) == 2
    finish(latest)
    assert set(Result.objects.values_list("revision", flat=True)) == {2}


def test_reimport_only_replaces_the_runs_of_markings_reading_changed_answers(
    client: Client, assessment: Assessment, user: User, idle_runner: FakeRunner
) -> None:
    q1_marking, q2_marking = full_marks_marking(assessment, "Q1"), full_marks_marking(assessment, "Q2")
    remark(assessment, ["Q1", "Q2"], user)
    first, untouched = active_run(q1_marking), active_run(q2_marking)
    upload(client, assessment)
    assert len(idle_runner.run_ids(REMARK)) == 2
    upload(client, assessment, EXAM_CSV.replace("photosynthesis uses light", "changed answer"))
    assert first.id in idle_runner.stopped
    assert active_run(q2_marking) == untouched
    assert len(idle_runner.run_ids(REMARK)) == 3


def test_deleted_owner_and_duplicate_callbacks_cannot_change_marks(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    run = schedule_marking(marking, user)
    assert run is not None
    submissions = marked_submissions(run)
    send_finished(run, submissions)
    before = results()
    send_finished(run, submissions)
    post_callback(run.id, {"event": "failed", "error": "Late failure"})
    assert results() == before
    marking.refresh_from_db()
    assert marking.failure == ""
    active = schedule_marking(marking, user)
    assert active is not None
    active_submissions = marked_submissions(active)
    delete_marking(marking)
    send_finished(active, active_submissions)
    assert not Result.objects.exists()


def test_incomplete_result_fails_without_replacing_existing_marks(assessment: Assessment, user: User) -> None:
    marking = full_marks_marking(assessment, "Q1")
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    newer = schedule_marking(marking, user)
    assert newer is not None
    send_finished(newer, marked_submissions(newer)[:1])
    marking.refresh_from_db()
    assert "incomplete" in marking.failure
    assert {str(revision) for revision in Result.objects.values_list("revision", flat=True)} == {first.key}


def test_an_invalid_marking_replaces_its_run_without_touching_its_marks(
    assessment: Assessment, user: User, idle_runner: FakeRunner
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
    previous = results()
    marking.definition = "uses: course-unavailable@1"
    marking.save()
    registered = list(idle_runner.launches)
    remark(assessment, ["Q2"], user)
    assert list(idle_runner.launches) == registered
    assert running.id in idle_runner.stopped
    assert results() == previous
    marking.refresh_from_db()
    assert marking.revision == int(running.key) + 1
    assert marking.questions_marked == ["Q1"], "it still owns its questions"
    assert untouched.id in idle_runner.held


def test_incompatible_question_settings_retire_running_revision_and_keep_previous_marks(
    assessment: Assessment, user: User, idle_runner: FakeRunner
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
    previous = results()
    running = schedule_marking(marking, user)
    assert running is not None
    submissions = marked_submissions(running)
    registered = list(idle_runner.launches)
    with transaction.atomic():
        assessment.lock()
        question.definition = {"type": "number", "max_marks": 1}
        question.save()
        remark(assessment, ["Q1"], user)
    assert list(idle_runner.launches) == registered
    assert running.id in idle_runner.stopped
    send_finished(running, submissions)
    assert results() == previous


def test_an_invalid_saved_definition_keeps_the_marks_of_every_question_it_marked(
    assessment: Assessment, user: User, idle_runner: FakeRunner
) -> None:
    marking = question_group_marking(assessment, ["Q1", "Q3"])
    first = schedule_marking(marking, user)
    assert first is not None
    finish(first)
    marking.refresh_from_db()
    marking.definition = "uses: course-unavailable@1"
    marking.save()
    remark(assessment, ["Q3"], user)
    assert idle_runner.run_ids(REMARK) == [first.id]
    marking.refresh_from_db()
    assert marking.questions_marked == ["Q1", "Q3"]
    assert Result.objects.count() == assessment.students.count() * 2
