import json
import re

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core.editing import DECISIONS, answer_decisions, dump_yaml

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.libraries.collection import in_use
from gradeflow_app.marking.answers import answer_groups
from gradeflow_app.marking.answers.column import _group_actions
from gradeflow_app.marking.models import Draft
from gradeflow_app.marking.workspace import Workspace

from .conftest import HTMX, start_marking
from .test_marking import tree

pytestmark = pytest.mark.django_db


def answer_card(page: str, answer: str) -> str:
    return next(card for card in re.split(r'<details class="group/answer', page)[1:] if f">{answer}</p>" in card)


@pytest.mark.parametrize(("choice", "color"), [("full", "success"), ("part", "warning"), ("not_allowed", "error")])
def test_decisions_lock_until_cancelled(client: Client, assessment: Assessment, choice: str, color: str) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    response = client.post(url, {"answer": ["42", "42.0"], "given": choice}, headers=HTMX)
    assert response.status_code == 200
    card = answer_card(response.content.decode(), "42")
    assert f"badge-{color}" in card
    label = next(label for decision, label in DECISIONS.items() if decision == choice)
    assert f">{label}</span>" in card
    assert "This answer group gets</span>" in card
    assert 'value="cancel">' in card
    assert 'name="given" value="full"' not in card
    assert 'name="given" value="part"' not in card
    assert 'name="given" value="not_allowed"' not in card
    assert 'value="none"' not in card

    before = tree(draft)
    for action in ("full", "part", "not_allowed"):
        again = client.post(url, {"answer": ["42.0"], "given": action}, headers=HTMX)
        assert again.status_code == 200
        assert 'value="cancel"' in answer_card(again.content.decode(), "42")
        assert ("X-Messages" in again.headers) == (action != choice)
        if action != choice:
            assert json.loads(again.headers["X-Messages"])[0]["level"] == "error"
        assert tree(draft) == before
    refreshed = client.get(reverse("answers", args=[assessment.pk, draft.question_id])).content.decode()
    assert 'value="cancel"' in answer_card(refreshed, "42")

    cancelled = client.post(url, {"answer": ["42.0"], "given": "cancel"}, headers=HTMX)
    assert cancelled.status_code == 200
    assert answer_decisions(tree(draft), methods=in_use(assessment.course)) == {}
    card = answer_card(cancelled.content.decode(), "42")
    assert 'name="given" value="full"' in card
    assert 'value="cancel"' not in card
    assert client.post(url, {"answer": ["42"], "given": "part"}).status_code == 200


def test_direct_form_edits_refresh_decisions(client: Client, assessment: Assessment) -> None:
    url, _ = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    added = client.post(url, {"path": "", "fields": "1", "answers": ["42"]}, headers=HTMX)
    assert 'value="cancel"' in answer_card(added.content.decode(), "42")
    removed = client.post(url, {"path": "", "fields": "1", "answers": []}, headers=HTMX)
    assert 'value="full"' in answer_card(removed.content.decode(), "42")


def test_saved_decisions_are_shown_and_survive_reopening(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    client.post(url, {"answer": ["41"], "given": "not_allowed"})
    client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {})
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).content.decode()
    assert f">{DECISIONS['not_allowed']}</span>" in answer_card(page, "41")
    assert 'value="cancel"' not in page
    client.post(reverse("marking_start", args=[assessment.pk, draft.question_id]), {})
    page = client.get(reverse("answers", args=[assessment.pk, draft.question_id])).content.decode()
    assert 'value="cancel"' in answer_card(page, "41")


def test_mixed_feedback_group_cannot_overwrite_an_existing_decision(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    client.post(url, {"answer": ["42"], "given": "full"})
    before = tree(draft)
    assert client.post(url, {"answer": ["41", "42"], "given": "part"}).status_code == 200
    assert tree(draft) == before
    assert draft.question_id is not None
    question = assessment.questions.get(pk=draft.question_id)
    group = answer_groups.AnswerGroup(key="same feedback", entries=answer_groups.entries(question))
    _group_actions(question, Workspace(Draft.objects.get(pk=draft.pk)), [group])
    assert [label for label, _ in group.decisions] == [DECISIONS["full"]]
    assert group.mixed_decisions, "a partly reviewed group must not be labelled simply Full marks"


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
    page = client.get(reverse("answers", args=[assessment.pk, draft.question_id])).content.decode()
    assert f">{DECISIONS['not_allowed']}</span>" in answer_card(page, "41")
    assert Draft.objects.get(pk=draft.pk).definition == definition
