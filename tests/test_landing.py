import json
from collections.abc import Iterator
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape
from gradeflow_core import MarkedSubmission, MarkScheme, QuestionSet, Submission, mark

from gradeflow_app.landing import content
from gradeflow_app.landing.content import MARKS, MarkingJson, landing_content
from gradeflow_app.landing.page import SECTIONS

pytestmark = pytest.mark.django_db


def test_visitors_see_what_gradeflow_does() -> None:
    response = Client().get(reverse("landing"))
    assert response.status_code == 200
    page = response.text
    for section in SECTIONS:
        assert f'id="{section}"' in page
    assert escape(landing_content().workspace.question_text) in page
    assert '<span class="code-k">def</span>' in page


def test_people_signed_in_go_to_their_courses(client: Client) -> None:
    assert client.get(reverse("landing")).headers["Location"] == reverse("courses")


def marked(
    question_set: QuestionSet, markings: list[MarkingJson], answers: list[dict[str, str]]
) -> Iterator[MarkedSubmission]:
    """Each student's `answers`, marked with `markings`."""
    submissions = [Submission(student_id=str(index), answers=answer) for index, answer in enumerate(answers)]
    return mark(question_set, MarkScheme.load(markings), submissions)


def marks_and_feedback(
    question_set: QuestionSet, markings: list[MarkingJson], answers: list[dict[str, str]], question: str
) -> list[tuple[float | None, str]]:
    """Each answer's marks for `question`, and its feedback."""
    results = [submission.results.get(question) for submission in marked(question_set, markings, answers)]
    return [(result.marks, result.feedback) if result else (None, "") for result in results]


def correct(
    question_set: QuestionSet, marking: MarkingJson, answers: list[dict[str, str]], question: str
) -> list[bool]:
    return [
        bool((result := submission.results.get(question)) and result.correct)
        for submission in marked(question_set, [marking], answers)
    ]


def workspace_marks() -> dict[str, Any]:
    """Each answer's marks, then which checks it's correct for, check by check."""
    workspace = landing_content().workspace
    answers = [{workspace.question_key: group.answer} for group in workspace.answer_groups]

    return {
        "marks": marks_and_feedback(workspace.question_set, [workspace.marking], answers, workspace.question_key),
        "correct": [
            correct(workspace.question_set, check.marking, answers, workspace.question_key)
            for check in workspace.checks
        ],
    }


def grouping_marks() -> dict[str, Any]:
    grouping = landing_content().grouping
    answers = [{grouping.question_key: answer.text} for answer in grouping.answers]
    return {
        option: marks_and_feedback(grouping.question_set, markings, answers, grouping.question_key)
        for option, markings in grouping.markings.items()
    }


def example_marks(key: str) -> dict[str, Any]:
    example = landing_content().examples[key]
    answers = [example_answer.answers for example_answer in example.answers]
    return {
        option: marks_and_feedback(example.question_set, markings, answers, example.marked_question)
        for option, markings in example.markings.items()
    }


def as_json(value: object) -> Any:
    return json.loads(json.dumps(value))


def core_marks() -> dict[str, Any]:
    examples = {key: example_marks(key) for key in landing_content().examples}
    return {"workspace": workspace_marks(), "grouping": grouping_marks(), "examples": examples}


def test_the_workspace_shows_the_marks_the_core_gives() -> None:
    assert as_json(workspace_marks()) == content.core_marks()["workspace"]


def test_grouping_shows_the_marks_the_core_gives() -> None:
    assert as_json(grouping_marks()) == content.core_marks()["grouping"]


@pytest.mark.parametrize("key", landing_content().examples)
def test_the_examples_show_the_marks_the_core_gives(key: str) -> None:
    assert as_json(example_marks(key)) == content.core_marks()["examples"][key]


if __name__ == "__main__":
    # Rewrites the marks; install the core's ml extra before running this script.
    MARKS.write_text(json.dumps(core_marks(), ensure_ascii=False, indent=1) + "\n")
