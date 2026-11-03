from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from gradeflow_core.editing import dump_yaml
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.models import MarkingDraftRecord
from tests.conftest import HTMX_HEADERS, NotifyingExecutor, start_marking


@pytest.mark.django_db(transaction=True)
def test_a_preview_is_remembered_before_it_can_finish(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job may finish, and the answers refresh, before the response that started it is sent."""
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
    remembered: list[bool] = []

    def submitted(job: Job) -> None:
        remembered.append(MarkingDraftRecord.objects.get(pk=draft.pk).preview_id == job.pk)

    monkeypatch.setattr(queue, "executor", lambda: NotifyingExecutor(submitted))
    client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]), headers=HTMX_HEADERS)
    assert remembered == [True]


@pytest.mark.django_db(transaction=True)
def test_previews_from_two_tabs_at_once_both_show(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tabs share a session, which each request saves whole: neither preview may undo the other's."""
    drafts = []
    for key in ("Q1", "Q2"):
        url, draft = start_marking(client, assessment, key, "gradeflow/accepted-answers@1")
        client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
        drafts.append(draft)
    other = Client()
    other.cookies = client.cookies
    started: list[Job] = []

    def submitted(job: Job) -> None:
        started.append(job)
        if len(started) == 1:  # the second tab previews while the first's request is still running
            other.post(reverse("draft_preview", args=[assessment.pk, drafts[1].pk]), headers=HTMX_HEADERS)

    monkeypatch.setattr(queue, "executor", lambda: NotifyingExecutor(submitted))
    client.post(reverse("draft_preview", args=[assessment.pk, drafts[0].pk]), headers=HTMX_HEADERS)
    assert len(started) == 2
    for draft in drafts:
        column = client.get(reverse("answers", args=[assessment.pk, draft.question_id]), headers=HTMX_HEADERS).content
        assert b"Waiting to start" in column, draft.question_id
        assert b"Marking changed" not in column, draft.question_id


@pytest.mark.django_db
def test_a_preview_the_sandbox_lost_can_be_tried_again(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
    preview_url = reverse("draft_preview", args=[assessment.pk, draft.pk])
    client.post(preview_url, headers=HTMX_HEADERS)
    job = Job.objects.get(kind=Job.Kind.PREVIEW)
    Job.objects.filter(pk=job.pk).update(status=Job.Status.RUNNING, started_at=timezone.now() - timedelta(days=1))
    column = client.get(reverse("answers", args=[assessment.pk, draft.question_id]), headers=HTMX_HEADERS).text
    assert "Preview failed: Ran past its time limit." in column
    with django_capture_on_commit_callbacks(execute=True):
        client.post(preview_url, headers=HTMX_HEADERS)
    assert Job.objects.filter(kind=Job.Kind.PREVIEW).count() == 2


@pytest.mark.django_db
@pytest.mark.parametrize("grouped", [False, True])
def test_a_preview_goes_stale_when_a_question_it_marked_changes(
    client: Client, assessment: Assessment, grouped: bool
) -> None:
    q1_check = {"question": "Q1", "uses": "gradeflow/accepted-answers@1", "with": {"answers": ["light"]}}
    if grouped:
        client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/answer-sets@1"})
        draft = MarkingDraftRecord.objects.get()
        answer_set = {"name": "Correct", "uses": "gradeflow/combination@1", "with": {"checks": [q1_check]}}
        draft.definition = dump_yaml({"uses": "gradeflow/answer-sets@1", "with": {"sets": [answer_set]}})
        answers_url = reverse("question_group_draft_answers", args=[assessment.pk, draft.pk])
    else:
        _, draft = start_marking(client, assessment, "Q1", "gradeflow/accepted-answers@1")
        draft.definition = dump_yaml(q1_check)
        answers_url = reverse("answers", args=[assessment.pk, draft.question_id])
    draft.save()
    client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]), headers=HTMX_HEADERS)
    assert b"Marking changed" not in client.get(answers_url, headers=HTMX_HEADERS).content
    question = assessment.questions.get(key="Q1")
    question.definition["reading"]["ignore_case"] = False
    question.save()
    assert b"Marking changed" in client.get(answers_url, headers=HTMX_HEADERS).content
