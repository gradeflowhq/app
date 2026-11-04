from collections.abc import Callable

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core.editing import dump_yaml
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs.runner import Ending, Launch, Run
from gradeflow_app.jobs.runs import ENDING_REASONS, UNAVAILABLE_REASON
from gradeflow_app.marking import previews
from gradeflow_app.marking.models import MarkingDraftRecord
from gradeflow_app.marking.workspace import PREVIEW
from tests.conftest import HTMX_HEADERS, FakeRunner, preview_draft, start_marking


def on_start(runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, started: Callable[[str], None]) -> None:
    """Call `started` with each run's ID once the runner holds it."""
    start = runner.start

    def start_and_tell(run: Run, launch: Launch) -> None:
        start(run, launch)
        started(run.id)

    monkeypatch.setattr(runner, "start", start_and_tell)


def answers_column(client: Client, draft: MarkingDraftRecord) -> str:
    return client.get(reverse("answers", args=[draft.assessment_id, draft.question_id]), headers=HTMX_HEADERS).text


@pytest.mark.django_db(transaction=True)
def test_a_preview_is_remembered_before_it_can_finish(
    client: Client, assessment: Assessment, idle_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The run may finish, and the answers refresh, before the response that started it is sent."""
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
    remembered: list[bool] = []
    on_start(
        idle_runner,
        monkeypatch,
        lambda run_id: remembered.append(
            previews.preview_state(MarkingDraftRecord.objects.get(pk=draft.pk)) is not None
        ),
    )
    client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]), headers=HTMX_HEADERS)
    assert remembered == [True]


@pytest.mark.django_db(transaction=True)
def test_previews_from_two_tabs_at_once_both_show(
    client: Client, assessment: Assessment, idle_runner: FakeRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tabs share a session, which each request saves whole: neither preview may undo the other's."""
    drafts = []
    for key in ("Q1", "Q2"):
        url, draft = start_marking(client, assessment, key, "gradeflow/accepted-answers@1")
        client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
        drafts.append(draft)
    other = Client()
    other.cookies = client.cookies
    started: list[str] = []

    def registered(run_id: str) -> None:
        started.append(run_id)
        if len(started) == 1:  # the second tab previews while the first's request is still running
            other.post(reverse("draft_preview", args=[assessment.pk, drafts[1].pk]), headers=HTMX_HEADERS)

    on_start(idle_runner, monkeypatch, registered)
    client.post(reverse("draft_preview", args=[assessment.pk, drafts[0].pk]), headers=HTMX_HEADERS)
    assert len(started) == 2
    for draft in drafts:
        column = answers_column(client, draft)
        assert "Waiting to start" in column, draft.question_id
        assert "Marking changed" not in column, draft.question_id


@pytest.mark.django_db
def test_a_preview_that_died_can_be_tried_again(
    client: Client,
    assessment: Assessment,
    idle_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
    preview_url = reverse("draft_preview", args=[assessment.pk, draft.pk])
    with django_capture_on_commit_callbacks(execute=True):
        client.post(preview_url, headers=HTMX_HEADERS)
    [run_id] = idle_runner.run_ids(PREVIEW)
    idle_runner.end(run_id)
    assert f"Preview failed: {ENDING_REASONS[Ending.FAILED]}" in answers_column(client, draft)
    with django_capture_on_commit_callbacks(execute=True):
        client.post(preview_url, headers=HTMX_HEADERS)
    assert len(idle_runner.run_ids(PREVIEW)) == 2


@pytest.mark.django_db
def test_a_preview_fails_while_the_runner_is_down(
    client: Client, assessment: Assessment, fake_runner: FakeRunner
) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["41"]})
    fake_runner.is_down = True
    preview_draft(client, draft)
    assert f"Preview failed: {UNAVAILABLE_REASON}" in answers_column(client, draft)
    fake_runner.is_down = False
    preview_draft(client, draft)
    draft.refresh_from_db()
    assert draft.preview_results is not None


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
    preview_draft(client, draft)
    assert b"Marking changed" not in client.get(answers_url, headers=HTMX_HEADERS).content
    question = assessment.questions.get(key="Q1")
    question.definition["reading"]["ignore_case"] = False
    question.save()
    assert b"Marking changed" in client.get(answers_url, headers=HTMX_HEADERS).content
