import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core import QuestionResult
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.answers.combinations import combinations
from gradeflow_app.marking.models import Draft
from gradeflow_app.marking.previews import results, stale
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.results.marks import awards
from gradeflow_app.results.models import Adjustment, Result
from tests.conftest import Sandbox, start_marking

pytestmark = pytest.mark.django_db


def preview(client: Client, assessment: Assessment, captured: DjangoCaptureOnCommitCallbacks) -> Draft:
    _, draft = start_marking(client, assessment, "Q1", "gradeflow/full-marks@1")
    with captured(execute=True):
        response = client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    assert response.status_code == 200
    draft.refresh_from_db()
    assert not stale(Workspace(draft))
    return draft


def test_previews_return_every_envelope_and_survive_cleared_input(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    draft = preview(client, assessment, django_capture_on_commit_callbacks)
    assert draft.preview_id is not None
    job = Job.objects.get(pk=draft.preview_id)
    assert job.input is None
    assert set(results(job)) == set(assessment.students.values_list("key", flat=True))
    client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 1


def test_a_changed_draft_previews_again_replacing_its_running_preview(
    client: Client,
    assessment: Assessment,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queue, "executor", lambda: Sandbox(events.started))
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields": "1", "answers": ["41"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    draft.refresh_from_db()
    assert draft.preview is not None
    first = draft.preview.pk
    assert not stale(Workspace(draft))
    client.post(url, {"path": "", "fields": "1", "answers": ["42"]})
    draft.refresh_from_db()
    assert stale(Workspace(draft))
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    draft.refresh_from_db()
    assert draft.preview_id != first
    assert not stale(Workspace(draft))
    replaced = Job.objects.get(pk=first)
    assert (replaced.status, replaced.error) == (Job.Status.FAILED, "Replaced by a newer run.")
    assert draft.preview is not None
    assert draft.preview.status == Job.Status.RUNNING


def test_projected_awards_preserve_independent_zero_and_empty_overrides(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.first()
    assert student is not None
    student.file_marks = {"Q1": 0.5}
    student.save()
    award = awards([question], [student])[student.pk, question.pk]
    assert award.marks == 0.5
    adjustment = Adjustment.objects.create(student=student, question=question, marks=0, feedback="")
    award = awards([question], [student])[student.pk, question.pk]
    projected = award.with_projected(
        QuestionResult(proportion=1, max_marks=question.core.max_marks, feedback="raw feedback", method="Full")
    )
    assert projected.marks == 0
    assert projected.feedback == ""
    adjustment.marks = None
    adjustment.feedback = "Keep this feedback"
    adjustment.save()
    projected = awards([question], [student])[student.pk, question.pk].with_projected(
        QuestionResult(proportion=1, max_marks=question.core.max_marks, method="Full")
    )
    assert projected.marks == question.core.max_marks
    assert projected.feedback == "Keep this feedback"


def test_group_changed_counts_compare_students_even_when_ranges_match(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    students = list(assessment.students.all()[:2])
    assessment.students.exclude(pk__in=[student.pk for student in students]).delete()
    for index, student in enumerate(students):
        student.answers = {"Q1": "same", "Q2": "same"}
        student.save()
        Result.objects.create(student=student, question=question, proportion=index, method="Before", revision=0)
    projected = {
        student.key: {"Q1": QuestionResult(proportion=1 - index, max_marks=question.core.max_marks, method="After")}
        for index, student in enumerate(students)
    }
    row = combinations(assessment, ["Q1"], projected, ["Q2"])[0]
    assert row.cells[0].marks == row.cells[0].preview == {0, 1}
    assert row.changes == 2
    assert row.changed
    Adjustment.objects.create(student=students[0], question=question, marks=0, feedback="")
    row = combinations(assessment, ["Q1"], projected, ["Q2"])[0]
    assert row.changes == 1


def test_group_current_awards_include_imported_marks_and_adjustments(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.first()
    assert student is not None
    assessment.students.exclude(pk=student.pk).delete()
    student.file_marks = {"Q1": 0.5}
    student.save()
    row = combinations(assessment, ["Q1"], None)[0]
    assert row.cells[0].marks == {0.5}
    Adjustment.objects.create(student=student, question=question, marks=0, feedback="Reviewed")
    row = combinations(assessment, ["Q1"], None)[0]
    assert row.cells[0].marks == {0}
    assert row.cells[0].feedback == "Reviewed"


def test_combinations_group_answers_the_question_reads_alike(assessment: Assessment) -> None:
    first = combinations(assessment, ["Q2"], None)[0]
    assert (first.cells[0].answer, first.count) == ("42", 5)
