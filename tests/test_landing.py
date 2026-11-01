import json
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape
from gradeflow_core import MarkScheme, QuestionSet, Submission, mark

from gradeflow_app.landing.content import MARKS, Marking, content
from gradeflow_app.landing.page import SECTIONS

pytestmark = pytest.mark.django_db


def test_visitors_see_what_gradeflow_does() -> None:
    response = Client().get(reverse("landing"))
    assert response.status_code == 200
    page = response.content.decode()
    for section in SECTIONS:
        assert f'id="{section}"' in page
    assert escape(content().workspace.question) in page


def test_people_signed_in_go_to_their_courses(client: Client) -> None:
    assert client.get(reverse("landing")).headers["Location"] == reverse("courses")


def marked(
    question_set: QuestionSet, markings: list[Marking], answers: list[dict[str, str]], question: str
) -> list[tuple[float | None, str]]:
    """Each answer's marks for `question`, and its feedback."""
    scheme = MarkScheme.load(markings)
    submissions = [Submission(student_id=str(index), answers=given) for index, given in enumerate(answers)]
    results = [submission.results.get(question) for submission in mark(question_set, scheme, submissions)]
    return [(result.marks, result.feedback) if result else (None, "") for result in results]


def correct(question_set: QuestionSet, marking: Marking, answers: list[dict[str, str]], question: str) -> list[bool]:
    scheme = MarkScheme.load([marking])
    submissions = [Submission(student_id=str(index), answers=given) for index, given in enumerate(answers)]
    return [
        bool((result := submission.results.get(question)) and result.correct)
        for submission in mark(question_set, scheme, submissions)
    ]


def workspace_marks() -> dict[str, Any]:
    """Each answer's marks, then which checks it's correct for, check by check."""
    workspace = content().workspace
    answers = [{workspace.number: group.code} for group in workspace.answers]

    return {
        "marks": marked(workspace.question_set, [workspace.marking], answers, workspace.number),
        "correct": [
            correct(workspace.question_set, check.check, answers, workspace.number) for check in workspace.checks
        ],
    }


def grouping_marks() -> dict[str, Any]:
    grouping = content().grouping
    answers = [{grouping.number: answer.text} for answer in grouping.answers]
    return {
        value: marked(grouping.question_set, markings, answers, grouping.number)
        for value, markings in grouping.markings.items()
    }


def example_marks(key: str) -> dict[str, Any]:
    example = content().examples[key]
    answers = [answered.answers for answered in example.answers]
    return {
        value: marked(example.question_set, markings, answers, example.marked)
        for value, markings in example.markings.items()
    }


def as_json(value: object) -> Any:
    return json.loads(json.dumps(value))


def core_marks() -> dict[str, Any]:
    examples = {key: example_marks(key) for key in content().examples}
    return {"workspace": workspace_marks(), "grouping": grouping_marks(), "examples": examples}


def stored() -> dict[str, Any]:
    marks: dict[str, Any] = json.loads(MARKS.read_text())
    return marks


def test_the_workspace_shows_the_marks_the_core_gives() -> None:
    assert as_json(workspace_marks()) == stored()["workspace"]


def test_grouping_shows_the_marks_the_core_gives() -> None:
    assert as_json(grouping_marks()) == stored()["grouping"]


@pytest.mark.parametrize("key", content().examples)
def test_the_examples_show_the_marks_the_core_gives(key: str) -> None:
    assert as_json(example_marks(key)) == stored()["examples"][key]


if __name__ == "__main__":
    # Rewrites the marks; install the core's ml extra before running this script.
    MARKS.write_text(json.dumps(core_marks(), ensure_ascii=False, indent=1) + "\n")
