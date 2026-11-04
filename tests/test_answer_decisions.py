import json
import re

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core.editing import DECISIONS, Decision, dump_yaml

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.models import MarkingDraftRecord
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.results.models import Result
from gradeflow_app.ui.middleware import MESSAGES_HEADER

from .conftest import HTMX_HEADERS, start_marking
from .test_marking import definition_tree

pytestmark = pytest.mark.django_db


def answer_card(page: str, answer: str) -> str:
    return next(card for card in re.split(r'<details class="group/answer', page)[1:] if f">{answer}</p>" in card)


@pytest.mark.parametrize(
    ("decision", "color"), [("full_marks", "success"), ("partial_marks", "warning"), ("not_allowed", "error")]
)
def test_decisions_lock_until_cancelled(client: Client, assessment: Assessment, decision: Decision, color: str) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    response = client.post(url, {"answer": ["42", "42.0"], "decision": decision}, headers=HTMX_HEADERS)
    assert response.status_code == 200
    card = answer_card(response.text, "42")
    assert f"badge-{color}" in card
    label = DECISIONS[decision]
    assert f">{label}</span>" in card
    assert "This answer group gets</span>" in card
    assert 'value="cancel">' in card
    assert 'name="decision" value="full_marks"' not in card
    assert 'name="decision" value="partial_marks"' not in card
    assert 'name="decision" value="not_allowed"' not in card
    assert 'value="none"' not in card

    before = definition_tree(draft)
    for second_decision in ("full_marks", "partial_marks", "not_allowed"):
        again = client.post(url, {"answer": ["42.0"], "decision": second_decision}, headers=HTMX_HEADERS)
        assert again.status_code == 200
        assert 'value="cancel"' in answer_card(again.text, "42")
        assert (MESSAGES_HEADER in again.headers) == (second_decision != decision)
        if second_decision != decision:
            assert json.loads(again.headers[MESSAGES_HEADER])[0]["level"] == "error"
        assert definition_tree(draft) == before
    refreshed = client.get(reverse("answers", args=[assessment.pk, draft.question_id])).text
    assert 'value="cancel"' in answer_card(refreshed, "42")

    cancelled = client.post(url, {"answer": ["42.0"], "decision": "cancel"}, headers=HTMX_HEADERS)
    assert cancelled.status_code == 200
    draft.refresh_from_db()
    assert MarkingWorkspace(draft).checks.answer_decisions() == {}
    card = answer_card(cancelled.text, "42")
    assert 'name="decision" value="full_marks"' in card
    assert 'value="cancel"' not in card
    assert client.post(url, {"answer": ["42"], "decision": "partial_marks"}).status_code == 200


def test_direct_form_edits_refresh_decisions(client: Client, assessment: Assessment) -> None:
    url, _ = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    added = client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]}, headers=HTMX_HEADERS)
    assert 'value="cancel"' in answer_card(added.text, "42")
    removed = client.post(url, {"path": "", "fields_posted": "1", "answers": []}, headers=HTMX_HEADERS)
    assert 'value="full_marks"' in answer_card(removed.text, "42")


def test_saved_decisions_are_shown_and_survive_reopening(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    client.post(url, {"answer": ["41"], "decision": "not_allowed"})
    client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {})
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).text
    assert f">{DECISIONS['not_allowed']}</span>" in answer_card(page, "41")
    assert 'value="cancel"' not in page
    client.post(reverse("marking_start", args=[assessment.pk, draft.question_id]), {})
    page = client.get(reverse("answers", args=[assessment.pk, draft.question_id])).text
    assert 'value="cancel"' in answer_card(page, "41")


def test_mixed_feedback_group_cannot_overwrite_an_existing_decision(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    client.post(url, {"answer": ["42"], "decision": "full_marks"})
    before = definition_tree(draft)
    assert client.post(url, {"answer": ["41", "42"], "decision": "partial_marks"}).status_code == 200
    assert definition_tree(draft) == before
    question = assessment.questions.get(key="Q2")
    for student in assessment.students.all():
        Result.objects.create(
            student=student, question=question, proportion=0, feedback="Wrong", method_label="M", revision=0
        )
    page = client.get(reverse("answers", args=[assessment.pk, question.pk]), {"group": "feedback"}).text
    card = answer_card(page, "42")
    assert "Mixed decisions" in card, "a partly reviewed group must not be labelled simply Full marks"
    assert 'value="cancel"' in card
    assert 'name="decision" value="partial_marks"' not in card


def test_yaml_decisions_are_derived_after_refresh(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    definition = dump_yaml(
        {
            "question": "Q2",
            "uses": "gradeflow/not-allowed@1",
            "with": {"check": {"uses": "gradeflow/accepted-answers@1", "with": {"answers": ["41"]}}},
        }
    )
    saved = client.post(reverse("draft_yaml", args=[assessment.pk, draft.pk]), {"definition": definition})
    assert saved.headers["HX-Refresh"] == "true"
    page = client.get(reverse("answers", args=[assessment.pk, draft.question_id])).text
    assert f">{DECISIONS['not_allowed']}</span>" in answer_card(page, "41")
    assert MarkingDraftRecord.objects.get(pk=draft.pk).definition == definition
