import json
import re
from collections.abc import Iterator
from html import unescape

import pytest
from django.test import Client
from django.urls import reverse
from gradeflow_core import BUILTINS, Marked, MarkScheme, QuestionResult, QuestionSet, Submission, mark
from gradeflow_core.editing import MarkingDraft, Position, compatible_methods

from gradeflow_app.editor.guide import choices
from gradeflow_app.editor.presenters import StepCard
from gradeflow_app.landing.by_example import CHAPTERS as BY_EXAMPLE_CHAPTERS
from gradeflow_app.landing.content import (
    CORE,
    CoreByExample,
    CoreByExampleStage,
    CoreEditingStage,
    CoreExample,
    CoreGroups,
    CoreHero,
    CoreOutput,
    CoreTrust,
    CoreTrustQuestion,
    CoreVariant,
    EditorContent,
    Example,
    ExampleVariant,
    LanguageModelResult,
    MarkingJson,
    MethodChoice,
    StepCards,
    TrustQuestion,
    core_output,
    landing_content,
)
from gradeflow_app.landing.groups import CHAPTERS as GROUPS_CHAPTERS
from gradeflow_app.landing.hero import CHAPTERS
from gradeflow_app.landing.page import SECTIONS
from gradeflow_app.landing.trust import CLAIMS
from gradeflow_app.libraries.collection import OfferedMethods
from gradeflow_app.marking.answers.answer_groups import StudentAnswer, group
from gradeflow_app.marking.answers.column import GROUPINGS
from gradeflow_app.marking.presenters import MarkingSummary, summary_of

pytestmark = pytest.mark.django_db
NO_SAVED_METHODS = OfferedMethods((), (), {})


def test_visitors_see_what_gradeflow_does() -> None:
    response = Client().get(reverse("landing"))
    assert response.status_code == 200
    page = response.text
    for section in SECTIONS:
        assert f'id="{section}"' in page
    assert '<span class="code-k">def</span>' in page


def test_the_hero_shows_an_assessment_from_its_import_to_canvas() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    hero = landing_content().hero
    description = hero.question_set[hero.question_key].description
    assert description
    expected_texts = [
        hero.file_name,
        description,
        *(stage.open_step.label for stage in core_output().hero.editing_stages if stage.open_step),
        hero.adjustment.feedback,
        *(student.student_id for student in hero.students),
        f"Sent {sum(answer.student_count for answer in hero.answers)} students' marks to “{hero.assessment}”.",
    ]
    for text in [*CHAPTERS, *expected_texts]:
        assert text in page


def test_the_hero_editor_hides_field_help() -> None:
    page = Client().get(reverse("landing")).text
    [editor, *_] = re.findall(r"<div [^>]*data-marking-editor>", page)
    assert "[&_.field-help]:hidden" in editor


def test_each_method_shows_every_change_to_its_settings() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    for key, example in landing_content().examples.items():
        assert f'data-example="{key}"' in page
        for variant in example.variants:
            assert variant.change in page


def test_the_trust_demo_shows_each_claim_on_its_own_answers() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    trust = landing_content().trust
    for text in [
        *CLAIMS,
        *(description for _, description in CLAIMS.values()),
        *(answer.text for answer in [*trust.same_mark.answers, *trust.breakdown.answers]),
        *(row.answers[key] for row in trust.ai.answers for key in row.answers),
        trust.student_names[0],
    ]:
        assert text in page


def test_the_demos_show_answers_grouped_and_marked_by_example() -> None:
    page = unescape(Client().get(reverse("landing")).text)
    content = landing_content()
    groups, by_example = content.groups, content.by_example
    for text in [*GROUPS_CHAPTERS, *(answer.text for answer in groups.answers), groups.adjustment.feedback]:
        assert text in page
    for text in [*BY_EXAMPLE_CHAPTERS, *(answer.text for answer in by_example.answers)]:
        assert text in page


def test_people_signed_in_go_to_their_courses(client: Client) -> None:
    assert client.get(reverse("landing")).headers["Location"] == reverse("courses")


def test_the_page_shows_what_the_core_gives() -> None:
    assert core_output() == generated()


def test_the_language_model_examples_end_as_the_cores_examples_do() -> None:
    for example in landing_content().examples.values():
        if (results := example.variants[-1].results) is None:
            continue
        [core_example] = [
            core_example
            for core_example in BUILTINS.find(example.method_ref).examples
            if core_example.with_ == example.variants[-1].marking["with"]
        ]
        [question] = example.question_set
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


def results(
    question_set: QuestionSet, marking: MarkingJson, answers: list[str], question: str
) -> dict[str, QuestionResult]:
    """Each answer's result for `question`, by answer."""
    marked_answers = marked(question_set, marking, [{question: answer} for answer in answers])
    return {answer: submission.results[question] for answer, submission in zip(answers, marked_answers, strict=True)}


def summary(marking: MarkingJson) -> MarkingSummary:
    return summary_of(MarkScheme.load([marking]).markings[0])


def draft_summary(question_set: QuestionSet, marking: MarkingJson) -> MarkingSummary:
    """The summary of `marking` as its editor shows it, which has none until its settings are complete."""
    draft = MarkingDraft(marking, question_set, marking.get("question"))
    return MarkingSummary(draft.label(""), draft.describe(""), needs_model_endpoint=draft.needs_model_endpoint(""))


def editor_content(question_set: QuestionSet, marking: MarkingJson, path: str = "") -> EditorContent:
    """What the editor shows of `marking`, or of its step at `path`."""
    draft = MarkingDraft(marking, question_set, marking.get("question"))
    return EditorContent(
        label=draft.label(path),
        description=draft.describe(path),
        form_schema=draft.schema(path),
        values=draft.values(path),
        steps=[
            StepCards(
                setting=setting,
                cards=[
                    StepCard(
                        path,
                        draft.label(path),
                        draft.describe(path),
                        False,
                        "",
                        draft.notes(path),
                        draft.needs_model_endpoint(path),
                    )
                    for path in setting.step_paths
                ],
            )
            for setting in draft.settings_holding_steps(path)
        ],
    )


def question_result(model_result: LanguageModelResult, max_marks: float, method_label: str) -> QuestionResult:
    """`model_result`, as the core gives it."""
    return QuestionResult(
        proportion=(model_result.marks or 0) / max_marks,
        max_marks=max_marks,
        feedback=model_result.feedback,
        method_label=method_label,
        is_marked=model_result.marks is not None,
    )


def core_hero() -> CoreHero:
    content = landing_content().hero
    question = content.question_set[content.question_key]
    saved_marking = content.editing_stages[-1].marking
    offered = choices(
        compatible_methods(Position(content.question_set, content.question_key, is_in_group=False)), NO_SAVED_METHODS
    )
    draft = MarkingDraft(saved_marking, content.question_set, content.question_key)
    [steps_setting] = draft.settings_holding_steps("")
    method_label = BUILTINS.find(saved_marking["uses"]).label
    return CoreHero(
        method_labels={other.question_key: BUILTINS.find(other.method_ref).label for other in content.other_questions},
        normalized_answers={answer.text: question.normalize(answer.text) for answer in content.answers},
        results={
            answer.text: question_result(answer.result, question.max_marks, method_label) for answer in content.answers
        },
        everyday=[MethodChoice.model_validate(method, from_attributes=True) for method in offered.everyday],
        advanced_choices=[MethodChoice.model_validate(method, from_attributes=True) for method in offered.advanced],
        step_choices=[
            MethodChoice.model_validate(method, from_attributes=True)
            for method in choices(draft.methods(steps_setting.new_step_path), NO_SAVED_METHODS).everyday
        ],
        editing_stages=[
            CoreEditingStage(
                marking=editor_content(content.question_set, stage.marking),
                open_step=editor_content(content.question_set, stage.marking, stage.open_step_path)
                if stage.open_step_path
                else None,
            )
            for stage in content.editing_stages
        ],
        summary=summary(saved_marking),
    )


def core_trust_question(content: TrustQuestion) -> CoreTrustQuestion:
    question = content.question_set[content.question_key]
    answers = [answer.text for answer in content.answers]
    return CoreTrustQuestion(
        normalized_answers={answer: question.normalize(answer) for answer in answers},
        results=results(content.question_set, content.marking, answers, content.question_key),
    )


def core_trust() -> CoreTrust:
    content = landing_content().trust
    return CoreTrust(
        same_mark=core_trust_question(content.same_mark),
        breakdown=core_trust_question(content.breakdown),
        ai=core_example(content.ai),
    )


def core_groups() -> CoreGroups:
    content = landing_content().groups
    question = content.question_set[content.question_key]
    answers = [answer.text for answer in content.answers]
    normalized = {answer: question.normalize(answer) for answer in answers}
    entries = [
        StudentAnswer(
            max_marks=question.max_marks, student_pk="", encrypted_student_id="", answer=answer, normalized=text
        )
        for answer, text in normalized.items()
    ]
    return CoreGroups(
        normalized_answers=normalized,
        results=results(content.question_set, content.marking, answers, content.question_key),
        spelling_groups=[
            [entry.answer for entry in spelling_group.student_answers]
            for spelling_group in group(entries, GROUPINGS["spelling"][1])
        ],
    )


def core_by_example() -> CoreByExample:
    content = landing_content().by_example
    question = content.question_set[content.question_key]
    answers = [answer.text for answer in content.answers]
    draft = MarkingDraft(content.marking, content.question_set, content.question_key)

    def stage() -> CoreByExampleStage:
        return CoreByExampleStage(
            results=results(content.question_set, draft.data, answers, content.question_key),
            summary=summary(draft.data),
            decisions=draft.answer_decisions(),
        )

    stages = [stage()]
    for answer, decision in content.decisions.items():
        draft.mark_by_example([answer], decision)
        stages.append(stage())
    return CoreByExample(normalized_answers={answer: question.normalize(answer) for answer in answers}, stages=stages)


def core_example(content: Example) -> CoreExample:
    method = BUILTINS.find(content.method_ref)
    answers = [answer.answers for answer in content.answers]

    def variant_results(variant: ExampleVariant) -> list[dict[str, QuestionResult]]:
        if variant.results is not None:
            [(key, question)] = content.question_set.items()
            return [{key: question_result(result, question.max_marks, method.label)} for result in variant.results]
        return [dict(submission.results) for submission in marked(content.question_set, variant.marking, answers)]

    return CoreExample(
        method_label=method.label,
        normalized_answers=[
            {key: content.question_set[key].normalize(answer) for key, answer in row.items()} for row in answers
        ],
        variants=[
            CoreVariant(summary=draft_summary(content.question_set, variant.marking), results=variant_results(variant))
            for variant in content.variants
        ],
    )


def other_methods() -> list[str]:
    """The labels of the methods for the page's questions without an example, simplest first."""
    content = landing_content()
    question_sets = [content.hero.question_set, content.groups.question_set, content.by_example.question_set]
    question_sets += [example.question_set for example in content.examples.values()]
    compatible = {
        method.ref: method
        for question_set in question_sets
        for question in [None, *question_set]
        for method in compatible_methods(Position(question_set, question, is_in_group=question is None))
    }
    with_examples = {BUILTINS.find(example.method_ref).label for example in content.examples.values()}
    by_level = sorted(compatible.values(), key=lambda method: method.level != "basic")
    return list(dict.fromkeys(method.label for method in by_level if method.label not in with_examples))


def generated() -> CoreOutput:
    return CoreOutput(
        hero=core_hero(),
        trust=core_trust(),
        groups=core_groups(),
        by_example=core_by_example(),
        examples={key: core_example(content) for key, content in landing_content().examples.items()},
        other_methods=other_methods(),
    )


if __name__ == "__main__":
    CORE.write_text(json.dumps(generated().model_dump(mode="json"), ensure_ascii=False, indent=1) + "\n")
