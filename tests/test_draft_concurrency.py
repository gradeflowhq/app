"""Independent requests editing the same draft must keep both changes."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import close_old_connections
from django.urls import reverse
from gradeflow_core.editing import answer_decisions

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.libraries.collection import course_methods
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.workspace import MarkingWorkspace

from .conftest import HTMX_HEADERS, signed_in_client, start_marking


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("same_answer", [False, True])
def test_concurrent_decisions_preserve_existing_choices(
    assessment: Assessment, monkeypatch: pytest.MonkeyPatch, same_answer: bool
) -> None:
    user = assessment.course.members.get(role="owner").user
    client = signed_in_client(user)
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    clients = [signed_in_client(user), signed_in_client(user)]
    start_together = threading.Barrier(2)
    save = MarkingWorkspace.save

    def delayed_save(workspace: MarkingWorkspace) -> None:
        time.sleep(0.1)
        save(workspace)

    monkeypatch.setattr(MarkingWorkspace, "save", delayed_save)

    def is_refused(index: int) -> bool:
        """Whether the decision was refused."""
        close_old_connections()
        try:
            start_together.wait(timeout=5)
            response = clients[index].post(
                url,
                {
                    "decision": "part_marks" if same_answer and index else "full_marks",
                    "answer": str(39 if same_answer else 39 + index),
                },
                headers=HTMX_HEADERS,
            )
            assert response.status_code == 200
            return "X-Messages" in response.headers
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(is_refused, range(2))) == ([False, True] if same_answer else [False, False])
    draft.refresh_from_db()
    decisions = answer_decisions(definition_data(draft.definition), methods=course_methods(assessment.course))
    assert set(decisions) == ({"39"} if same_answer else {"39", "40"})
    assert decisions["39"] in ("part_marks", "full_marks")
