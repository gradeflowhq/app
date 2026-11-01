import pytest
from django.test import Client

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.checks import tree_of
from tests.conftest import start_marking


@pytest.mark.django_db
def test_feedback_written_in_the_boxes_marks_with_them(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    page = client.get(url).content.decode()
    assert "When it&#x27;s partly correct" in page
    posted = {
        "path": "",
        "fields": "1",
        "points": ["light"],
        "feedback-correct": "Good.",
        "feedback-partly_correct": "",
        "feedback-incorrect": "",
    }
    client.post(url, posted)
    draft.refresh_from_db()
    assert tree_of(draft.definition)["feedback"] == {"correct": "Good."}, "an empty box keeps the method's feedback"
    again = client.get(url).content.decode()
    assert ">Good.</textarea>" in again
