import re
from typing import TYPE_CHECKING, Any

import pytest
from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core.editing import load_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.marking.workspace import DRAFTING, PREVIEW
from gradeflow_app.questions.models import Question
from tests.conftest import FakeRunner, full_marks_marking, post_callback, sandbox_tasks
from tests.test_model_service import (
    MODEL_SERVICE_HEADERS,
    PROVIDER,
    PROVIDER_URL,
    endpoint_sent,
    model_service,
    run_input,
)

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as Response

INSTRUCTIONS = "1 mark for saying photosynthesis uses light."
LIGHT = {"question": "Q1", "uses": "gradeflow/accepted-answers@1", "with": {"answers": ["photosynthesis uses light"]}}


@pytest.fixture(autouse=True)
def _offer_provider(settings: Settings) -> None:
    settings.GRADEFLOW_MODEL_PROVIDERS = {PROVIDER: PROVIDER_URL}


@pytest.fixture
def q1(assessment: Assessment) -> Question:
    return assessment.questions.get(key="Q1")


def post(client: Client, question: Question, name: str, data: dict[str, str] | None = None) -> Response:
    """POST as a browser connected to the AI service, running on-commit callbacks so runs start or stop."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        url = reverse(name, args=[question.assessment_id, question.pk])
        return client.post(url, data or {}, headers=MODEL_SERVICE_HEADERS)


def start_drafting(client: Client, question: Question, instructions: str = INSTRUCTIONS) -> Response:
    return post(client, question, "marking_drafting", {"instructions": instructions})


def finish_drafting(run_id: str, marking: dict[str, Any] = LIGHT, summary: str = "Accepts one answer.") -> None:
    with TestCase.captureOnCommitCallbacks(execute=True):
        assert post_callback(run_id, {"event": "finished", "outcome": {"marking": marking, "summary": summary}}) == 204


def panel(client: Client, question: Question) -> str:
    return client.get(reverse("marking", args=[question.assessment_id, question.pk])).text


def draft_record(question: Question) -> MarkingDraftRecord:
    return MarkingDraftRecord.objects.get(question=question)


def test_the_panel_drafts_new_markings_and_updates_saved_ones_from_their_instructions(
    client: Client, assessment: Assessment, q1: Question
) -> None:
    drafting_url = reverse("marking_drafting", args=[q1.assessment_id, q1.pk])
    page = panel(client, q1)
    assert "Draft with AI" in page
    assert f'hx-post="{drafting_url}"' in page
    assert 'aria-label="Draft marking"' in page
    marking = full_marks_marking(assessment, "Q1")
    marking.instructions = INSTRUCTIONS
    marking.save()
    saved = panel(client, q1)
    assert INSTRUCTIONS in saved, "the saved marking shows its instructions"
    assert drafting_url not in saved, "a saved marking is updated from its draft"
    post(client, q1, "marking_start")
    editor = panel(client, q1)
    assert "Ask AI" not in editor, "with instructions, the band has its own Edit"
    assert 'aria-label="Update marking"' in editor
    assert f">{INSTRUCTIONS}</textarea>" in editor


def test_a_marking_without_instructions_offers_ask_ai_beside_change(
    client: Client, assessment: Assessment, q1: Question
) -> None:
    full_marks_marking(assessment, "Q1")
    post(client, q1, "marking_start")
    editor = panel(client, q1)
    assert "Ask AI" in editor
    assert re.search(r'id="marking-instructions" class="panel-band"[^>]* hidden>', editor), "no band until it's opened"


@pytest.mark.usefixtures("idle_runner")
def test_removing_the_instructions_leaves_the_marking(
    client: Client, user: User, assessment: Assessment, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    full_marks_marking(assessment, "Q1")
    start_drafting(client, q1)
    finish_drafting(fake_runner.run_ids(DRAFTING)[0])
    assert "Remove the marking instructions?" in panel(client, q1)
    page = post(client, q1, "marking_instructions_remove").text
    draft = draft_record(q1)
    assert (draft.instructions, draft.drafting_summary) == ("", "")
    assert load_yaml(draft.definition) == LIGHT, "the marking stays"
    assert "Ask AI" in page, "Ask AI is back beside Change"


@pytest.mark.usefixtures("idle_runner")
def test_removing_a_new_markings_instructions_ends_its_draft(client: Client, user: User, q1: Question) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    page = post(client, q1, "marking_drafting_cancel").text
    assert "Remove the marking instructions?" in page
    assert "The marking stays as it is." not in page, "there's no marking yet"
    post(client, q1, "marking_instructions_remove")
    assert not MarkingDraftRecord.objects.exists()


def test_drafting_needs_instructions_the_ai_service_and_a_draft_model(client: Client, user: User, q1: Question) -> None:
    url = reverse("marking_drafting", args=[q1.assessment_id, q1.pk])
    page = client.post(url, {"instructions": INSTRUCTIONS}).text
    assert "Connect your AI service to draft with it." in page
    assert "Go to Integrations" in page
    assert f">{INSTRUCTIONS}</textarea>" in page, "what was typed stays"
    service = model_service(user, fast_model="decider", chosen_by="myself")
    assert "Choose a draft model for your AI service." in start_drafting(client, q1).text
    service.draft_model = "drafter"
    service.save()
    blank = start_drafting(client, q1, "  \n ").text
    assert "Write your marking instructions." in blank
    assert "Go to Integrations" not in blank
    assert not MarkingDraftRecord.objects.exists()


@pytest.mark.usefixtures("idle_runner")
def test_drafting_starts_a_draft_from_the_instructions_and_locks_the_panel(
    client: Client, user: User, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    page = panel(client, q1)
    draft = draft_record(q1)
    assert draft.instructions == INSTRUCTIONS
    [run_id] = fake_runner.run_ids(DRAFTING)
    assert endpoint_sent(fake_runner, run_id)["draft_model"] == "drafter"
    assert "Marking instructions" in page
    assert "Waiting to start…" in page
    assert "1 mark for saying photosynthesis uses light.</p>" in page
    assert "Choose a method" not in page, "the methods wait for AI"
    sent = run_input(run_id)
    assert (sent["question"], sent["instructions"], sent["marking"], sent["methods"]) == ("Q1", INSTRUCTIONS, None, [])
    assert all(submission["answers"].keys() == {"Q1"} for submission in sent["submissions"])


@pytest.mark.usefixtures("idle_runner")
def test_updating_sends_the_saved_marking_and_shows_how_far_ai_got(
    client: Client, user: User, assessment: Assessment, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    full_marks_marking(assessment, "Q1")
    start_drafting(client, q1)
    [run_id] = fake_runner.run_ids(DRAFTING)
    assert run_input(run_id)["marking"] == {"question": "Q1", "uses": "gradeflow/full-marks@1"}
    assert post_callback(run_id, {"event": "stage", "text": "Checking it on 10 answers"}) == 204
    page = panel(client, q1)
    assert "Checking it on 10 answers…" in page
    assert re.search(r"<div inert class=\"opacity-60\">", page), "the marking waits for AI"
    assert re.search(r"<button [^>]*btn-primary[^>]*disabled", page), "saving waits for AI"


@pytest.mark.usefixtures("idle_runner")
def test_the_marking_ai_writes_replaces_the_draft_and_says_what_it_did(
    client: Client, user: User, assessment: Assessment, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    full_marks_marking(assessment, "Q1")
    start_drafting(client, q1)
    [run_id] = fake_runner.run_ids(DRAFTING)
    finish_drafting(run_id)
    assert load_yaml(draft_record(q1).definition) == LIGHT
    assert fake_runner.run_ids(PREVIEW), "previewed at once"
    assert "Accepts one answer." in panel(client, q1)
    page = post(client, q1, "marking_drafting_summary_dismiss").text
    assert "Accepts one answer." not in page
    assert load_yaml(draft_record(q1).definition) == LIGHT


@pytest.mark.usefixtures("idle_runner")
def test_a_new_run_clears_the_summary(client: Client, user: User, q1: Question, fake_runner: FakeRunner) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    finish_drafting(fake_runner.run_ids(DRAFTING)[0])
    start_drafting(client, q1)
    post(client, q1, "marking_drafting_cancel")
    assert "Accepts one answer." not in panel(client, q1)


@pytest.mark.usefixtures("idle_runner")
def test_a_change_to_the_draft_clears_the_summary(
    client: Client, user: User, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    finish_drafting(fake_runner.run_ids(DRAFTING)[0])
    draft = draft_record(q1)
    url = reverse("draft", args=[q1.assessment_id, draft.pk])
    page = client.post(url, {"path": "", "fields_posted": "1", "answers": ["light"]}).text
    draft.refresh_from_db()
    assert draft.drafting_summary == ""
    assert '<div id="marking-instructions" hx-swap-oob="outerMorph"' in page
    assert "Accepts one answer." not in page
    page = client.post(url, {"path": "", "fields_posted": "1", "answers": ["light", "sun"]}).text
    assert 'id="marking-instructions"' not in page, "the next change doesn't send the band again"


@pytest.mark.usefixtures("idle_runner")
def test_a_change_to_the_drafts_yaml_clears_the_summary(
    client: Client, user: User, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    finish_drafting(fake_runner.run_ids(DRAFTING)[0])
    draft = draft_record(q1)
    client.post(
        reverse("draft_yaml", args=[q1.assessment_id, draft.pk]), {"definition": "uses: gradeflow/full-marks@1"}
    )
    draft.refresh_from_db()
    assert draft.drafting_summary == ""


@pytest.mark.usefixtures("idle_runner")
def test_a_failed_drafting_says_why(client: Client, user: User, q1: Question, fake_runner: FakeRunner) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    run_id = fake_runner.run_ids(DRAFTING)[0]
    with TestCase.captureOnCommitCallbacks(execute=True):
        post_callback(run_id, {"event": "failed", "error": "AI wrote no usable marking."})
    page = panel(client, q1)
    assert "AI wrote no usable marking." in page
    assert f">{INSTRUCTIONS}</textarea>" in page, "open, to send again"


@pytest.mark.usefixtures("idle_runner")
def test_a_drafted_marking_the_app_cant_load_fails(
    client: Client, user: User, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    finish_drafting(fake_runner.run_ids(DRAFTING)[0], {"question": "Q1", "uses": "gradeflow/nothing@1"})
    assert draft_record(q1).drafting_failure


@pytest.mark.usefixtures("idle_runner")
def test_cancelling_stops_ai_and_a_late_marking_is_ignored(
    client: Client, user: User, assessment: Assessment, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    full_marks_marking(assessment, "Q1")
    start_drafting(client, q1)
    run_id = fake_runner.run_ids(DRAFTING)[0]
    post(client, q1, "marking_drafting_cancel")
    assert run_id in fake_runner.stopped
    finish_drafting(run_id)
    draft = draft_record(q1)
    assert load_yaml(draft.definition)["uses"] == "gradeflow/full-marks@1"
    assert 'aria-label="Update marking"' in panel(client, q1)


@pytest.mark.usefixtures("idle_runner")
def test_cancelling_a_new_marking_keeps_its_instructions(client: Client, user: User, q1: Question) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    page = post(client, q1, "marking_drafting_cancel").text
    assert "Choose a method" in page
    assert draft_record(q1).instructions == INSTRUCTIONS


@pytest.mark.usefixtures("idle_runner")
def test_the_instructions_are_saved_with_the_marking_and_each_version(
    client: Client, user: User, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    finish_drafting(fake_runner.run_ids(DRAFTING)[0])
    draft = draft_record(q1)
    post(client, q1, "marking_drafting", {"instructions": "Light, for 1 mark."})
    finish_drafting(fake_runner.run_ids(DRAFTING)[1])
    with TestCase.captureOnCommitCallbacks(execute=True):
        client.post(reverse("draft_save", args=[q1.assessment_id, draft.pk]), {"path": "", "operation": "save"})
    marking = Marking.objects.get(question=q1)
    assert marking.instructions == "Light, for 1 mark."
    assert marking.history.latest().instructions == "Light, for 1 mark."
    assert "Light, for 1 mark." in panel(client, q1), "shown with the saved marking"
    post(client, q1, "marking_start")
    assert draft_record(q1).instructions == "Light, for 1 mark."
    client.post(reverse("draft_discard", args=[q1.assessment_id, draft_record(q1).pk]))
    history_id = marking.history.latest().history_id
    client.post(reverse("marking_restore", args=[q1.assessment_id, q1.pk, history_id]))
    assert draft_record(q1).instructions == "Light, for 1 mark."


def test_the_sandbox_drafts_with_the_core(monkeypatch: pytest.MonkeyPatch) -> None:
    import gradeflow_core
    from gradeflow_core import Drafted, MarkScheme

    calls: list[dict[str, Any]] = []

    def draft_marking(*args: Any, **options: Any) -> Drafted:
        calls.append({"args": args, **options})
        options["on_stage"]("Drafting the marking")
        return Drafted(MarkScheme.load([LIGHT]).markings[0], "Accepts one answer.")

    monkeypatch.setattr(gradeflow_core, "draft_marking", draft_marking)
    events: list[Any] = []
    payload = {
        "questions": {"Q1": {"type": "text"}},
        "question": "Q1",
        "instructions": INSTRUCTIONS,
        "marking": {"question": "Q1", "uses": "gradeflow/full-marks@1"},
        "methods": [],
        "submissions": [{"student_id": "1", "answers": {"Q1": "light"}}],
    }
    outcome = sandbox_tasks(events.append)["draft"](payload)
    assert outcome == {"marking": LIGHT, "summary": "Accepts one answer."}
    assert events == [{"event": "stage", "text": "Drafting the marking"}]
    [call] = calls
    assert call["args"][1:3] == ("Q1", INSTRUCTIONS)
    assert call["marking"].uses == "gradeflow/full-marks@1"


@pytest.mark.usefixtures("idle_runner")
def test_a_drafting_run_the_runner_lost_can_be_tried_again(
    client: Client, user: User, q1: Question, fake_runner: FakeRunner
) -> None:
    model_service(user, draft_model="drafter", chosen_by="myself")
    start_drafting(client, q1)
    fake_runner.held.clear()
    page = panel(client, q1)
    assert "The marking service stopped responding." in page
    assert f">{INSTRUCTIONS}</textarea>" in page, "open, to send again"
