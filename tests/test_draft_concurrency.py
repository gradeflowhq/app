"""Independent requests editing the same draft must keep both changes."""

from functools import partial

import pytest
from django.urls import reverse

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.ui.middleware import MESSAGES_HEADER

from .conftest import HTMX_HEADERS, race, signed_in_client, start_marking


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_answer", [False, True])
def test_concurrent_decisions_preserve_existing_choices(assessment: Assessment, same_answer: bool) -> None:
    user = assessment.course.members.get(role="owner").user
    client = signed_in_client(user)
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    clients = [signed_in_client(user), signed_in_client(user)]

    def is_refused(index: int) -> bool:
        """Whether the decision was refused."""
        response = clients[index].post(
            url,
            {
                "decision": "partial_marks" if same_answer and index else "full_marks",
                "answer": str(39 if same_answer else 39 + index),
            },
            headers=HTMX_HEADERS,
        )
        assert response.status_code == 200
        return MESSAGES_HEADER in response.headers

    refused = race(partial(is_refused, 0), partial(is_refused, 1))
    assert refused == ((False, True) if same_answer else (False, False))
    draft.refresh_from_db()
    decisions = MarkingWorkspace(draft).draft.answer_decisions()
    assert set(decisions) == ({"39"} if same_answer else {"39", "40"})
    assert decisions["39"] == "full_marks"
