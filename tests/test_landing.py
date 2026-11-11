import json
from collections.abc import Iterator

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core import BUILTINS, Marked, MarkScheme, QuestionResult, QuestionSet, Submission, mark
from gradeflow_core.editing import MarkingDraft, Position, compatible_methods

from gradeflow_app.editor.presenters import StepCard
from gradeflow_app.landing.content import (
    CORE,
    CoreExample,
    CoreOutput,
    CoreReview,
    CoreVariant,
    CoreWorkspace,
    EditorContent,
    Example,
    LanguageModelResult,
    MarkingJson,
    StepCards,
    core_output,
    landing_content,
)
from gradeflow_app.landing.page import SECTIONS
from gradeflow_app.marking.presenters import MarkingSummary, summary_of

pytestmark = pytest.mark.django_db


def test_visitors_see_what_gradeflow_does() -> None:
    response = Client().get(reverse("landing"))
    assert response.status_code == 200
    page = response.text
    for section in SECTIONS:
        assert f'id="{section}"' in page
    assert "Write a recursive function sum_digits(n)" in page
    assert '<span class="code-k">def</span>' in page


def test_people_signed_in_go_to_their_courses(client: Client) -> None:
    assert client.get(reverse("landing")).headers["Location"] == reverse("courses")


def test_the_page_shows_what_the_core_gives() -> None:
    assert core_output() == generated()


def test_answers_grouped_by_feedback_get_the_same_feedback() -> None:
    for group in core_output().workspace.results:
        assert len({result.feedback for result in group}) == 1


def test_the_language_model_examples_end_as_the_cores_examples_do() -> None:
    for example in landing_content().examples.values():
        if (results := example.variants[-1].results) is None:
            continue
        [core_example] = [
            core_example
            for core_example in BUILTINS.find(example.method).examples
            if core_example.with_ == example.variants[-1].marking["with"]
        ]
        question = example.marked_question
        expected = {row.answers[question]: row.expected[question] for row in core_example.rows}
        assert {
            answer.answers[question]: (result.marks, result.feedback)
            for answer, result in zip(example.answers, results, strict=True)
        } == {answer: (row.marks, row.feedback) for answer, row in expected.items()}


# What the core gives for the page's content; running this file writes it to core.json.


def marked(
    question_set: QuestionSet, marking: MarkingJson, answers: list[dict[str, str]]
) -> Iterator[Marked[Submission]]:
    """Each student's `answers`, marked with `marking`."""
    submissions = [Submission(student_id=str(index), answers=answer) for index, answer in enumerate(answers)]
    return mark(question_set, MarkScheme.load([marking]), submissions)


def results(question_set: QuestionSet, marking: MarkingJson, answers: list[str], question: str) -> list[QuestionResult]:
    """Each answer's result for `question`."""
    return [
        submission.results[question]
        for submission in marked(question_set, marking, [{question: answer} for answer in answers])
    ]


def summary(marking: MarkingJson) -> MarkingSummary:
    return summary_of(MarkScheme.load([marking]).markings[0])


def editor_content(question_set: QuestionSet, marking: MarkingJson) -> EditorContent:
    """What the editor shows of `marking`."""
    draft = MarkingDraft(marking, question_set, marking.get("question"))
    return EditorContent(
        label=draft.label(""),
        description=draft.describe(""),
        form_schema=draft.schema(""),
        values=draft.values(""),
        steps=[
            StepCards(
                setting=setting,
                cards=[
                    StepCard(path, draft.label(path), draft.describe(path), False, "", draft.notes(path))
                    for path in setting.step_paths
                ],
            )
            for setting in draft.settings_holding_steps("")
        ],
    )


def workspace() -> CoreWorkspace:
    content = landing_content().workspace
    question = content.question_set[content.question_key]
    methods = [item.method for item in content.questions if item.method]
    methods += [group.method for group in content.question_groups]
    return CoreWorkspace(
        method_labels={method: BUILTINS.find(method).label for method in methods},
        normalized=[[question.normalize(answer.text) for answer in group] for group in content.answer_groups],
        results=[
            results(content.question_set, content.marking, [answer.text for answer in group], content.question_key)
            for group in content.answer_groups
        ],
        summary=summary(content.marking),
        editor=editor_content(content.question_set, content.marking),
        edited_editor=editor_content(content.question_set, content.edited_marking),
        edited_results=results(
            content.question_set,
            content.edited_marking,
            [answer.text for group in content.answer_groups for answer in group],
            content.question_key,
        ),
        edited_summary=summary(content.edited_marking),
    )


def review() -> CoreReview:
    content = landing_content().review
    question = content.question_set[content.question_key]
    draft = MarkingDraft(content.marking, content.question_set, content.question_key)
    for decision in content.decisions:
        draft.mark_by_example(decision.answers, decision.decision)
    answers = [answer.text for answer in content.answers]
    return CoreReview(
        normalized=[question.normalize(answer) for answer in answers],
        results=results(content.question_set, content.marking, answers, content.question_key),
        summary=summary(content.marking),
        editor=editor_content(content.question_set, content.marking),
        decided_results=results(content.question_set, draft.data, answers, content.question_key),
        decided_summary=summary(draft.data),
        decided_editor=editor_content(content.question_set, draft.data),
        decisions=draft.answer_decisions(),
    )


def example(content: Example) -> CoreExample:
    method = BUILTINS.find(content.method)
    answers = [answer.answers for answer in content.answers]

    def variant_results(marking: MarkingJson, given: list[LanguageModelResult] | None) -> list[QuestionResult]:
        if given is not None:
            max_marks = content.max_marks
            return [
                QuestionResult(
                    proportion=(result.marks or 0) / max_marks,
                    max_marks=max_marks,
                    feedback=result.feedback,
                    method_label=method.label,
                    is_marked=result.marks is not None,
                )
                for result in given
            ]
        return [
            submission.results[content.marked_question] for submission in marked(content.question_set, marking, answers)
        ]

    return CoreExample(
        method_label=method.label,
        method_summary=method.summary,
        variants=[
            CoreVariant(
                description=MarkingDraft(
                    variant.marking, content.question_set, variant.marking.get("question")
                ).describe(""),
                results=variant_results(variant.marking, variant.results),
            )
            for variant in content.variants
        ],
    )


def other_methods() -> list[str]:
    """The labels of the methods for the page's questions without an example, simplest first."""
    content = landing_content()
    question_sets = [content.workspace.question_set, content.review.question_set]
    question_sets += [example.question_set for example in content.examples.values()]
    compatible = {
        method.ref: method
        for question_set in question_sets
        for question in [None, *question_set]
        for method in compatible_methods(Position(question_set, question, is_in_group=question is None))
    }
    with_examples = {BUILTINS.find(example.method).label for example in content.examples.values()}
    by_level = sorted(compatible.values(), key=lambda method: method.level != "basic")
    return list(dict.fromkeys(method.label for method in by_level if method.label not in with_examples))


def generated() -> CoreOutput:
    return CoreOutput(
        workspace=workspace(),
        review=review(),
        examples={key: example(content) for key, content in landing_content().examples.items()},
        other_methods=other_methods(),
    )


if __name__ == "__main__":
    CORE.write_text(json.dumps(generated().model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n")
