import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core import QuestionResult
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.answers.combinations import combinations
from gradeflow_app.marking.models import MarkingDraftRecord
from gradeflow_app.marking.previews import preview_is_stale, preview_results
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.results.marks import awards
from gradeflow_app.results.models import Adjustment, Result
from tests.conftest import NotifyingExecutor, start_marking

pytestmark = pytest.mark.django_db


def previewed_draft(
    client: Client, assessment: Assessment, capture_on_commit: DjangoCaptureOnCommitCallbacks
) -> MarkingDraftRecord:
    _, draft = start_marking(client, assessment, "Q1", "gradeflow/full-marks@1")
    with capture_on_commit(execute=True):
        response = client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    assert response.status_code == 200
    draft.refresh_from_db()
    assert not preview_is_stale(MarkingWorkspace(draft))
    return draft


def test_previews_return_every_encrypted_id_and_survive_cleared_input(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    draft = previewed_draft(client, assessment, django_capture_on_commit_callbacks)
    assert draft.preview_id is not None
    job = Job.objects.get(pk=draft.preview_id)
    assert job.input is None
    assert set(preview_results(job)) == set(assessment.students.values_list("encrypted_id", flat=True))
    client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 1


def test_a_changed_draft_previews_again_replacing_its_running_preview(
    client: Client,
    assessment: Assessment,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(queue, "executor", lambda: NotifyingExecutor(events.apply_started))
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    draft.refresh_from_db()
    assert draft.preview is not None
    first = draft.preview.pk
    assert not preview_is_stale(MarkingWorkspace(draft))
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]})
    draft.refresh_from_db()
    assert preview_is_stale(MarkingWorkspace(draft))
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    draft.refresh_from_db()
    assert draft.preview_id != first
    assert not preview_is_stale(MarkingWorkspace(draft))
    replaced = Job.objects.get(pk=first)
    assert (replaced.status, replaced.error) == (Job.Status.FAILED, "Replaced by a newer run.")
    assert draft.preview is not None
    assert draft.preview.status == Job.Status.RUNNING


def test_awards_with_a_preview_preserve_independent_zero_and_empty_overrides(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.first()
    assert student is not None
    student.imported_marks = {"Q1": 0.5}
    student.save()
    award = awards([question], [student])[student.pk, question.pk]
    assert award.marks == 0.5
    adjustment = Adjustment.objects.create(student=student, question=question, marks=0, feedback="")
    award = awards([question], [student])[student.pk, question.pk]
    previewed_award = award.with_preview(
        QuestionResult(
            proportion=1, max_marks=question.core_question.max_marks, feedback="raw feedback", method_label="Full"
        )
    )
    assert previewed_award.marks == 0
    assert previewed_award.feedback == ""
    adjustment.marks = None
    adjustment.feedback = "Keep this feedback"
    adjustment.save()
    previewed_award = awards([question], [student])[student.pk, question.pk].with_preview(
        QuestionResult(proportion=1, max_marks=question.core_question.max_marks, method_label="Full")
    )
    assert previewed_award.marks == question.core_question.max_marks
    assert previewed_award.feedback == "Keep this feedback"


def test_answer_group_changed_counts_compare_students_even_when_ranges_match(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    students = list(assessment.students.all()[:2])
    assessment.students.exclude(pk__in=[student.pk for student in students]).delete()
    for index, student in enumerate(students):
        student.answers = {"Q1": "same", "Q2": "same"}
        student.save()
        Result.objects.create(student=student, question=question, proportion=index, method="Before", revision=0)
    results_by_student = {
        student.encrypted_id: {
            "Q1": QuestionResult(proportion=1 - index, max_marks=question.core_question.max_marks, method_label="After")
        }
        for index, student in enumerate(students)
    }
    row = combinations(assessment, ["Q1"], results_by_student, ["Q2"])[0]
    assert row.cells[0].marks == row.cells[0].preview_marks == {0, 1}
    assert row.changed_count == 2
    assert row.changed
    Adjustment.objects.create(student=students[0], question=question, marks=0, feedback="")
    row = combinations(assessment, ["Q1"], results_by_student, ["Q2"])[0]
    assert row.changed_count == 1


def test_answer_group_current_awards_include_imported_marks_and_adjustments(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.first()
    assert student is not None
    assessment.students.exclude(pk=student.pk).delete()
    student.imported_marks = {"Q1": 0.5}
    student.save()
    row = combinations(assessment, ["Q1"], None)[0]
    assert row.cells[0].marks == {0.5}
    Adjustment.objects.create(student=student, question=question, marks=0, feedback="Reviewed")
    row = combinations(assessment, ["Q1"], None)[0]
    assert row.cells[0].marks == {0}
    assert row.cells[0].feedback == "Reviewed"


def test_combinations_group_answers_the_question_reads_alike(assessment: Assessment) -> None:
    first = combinations(assessment, ["Q2"], None)[0]
    assert (first.cells[0].answer, first.student_count) == ("42", 5)
