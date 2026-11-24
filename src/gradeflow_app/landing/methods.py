"""The method demos: an example of each method, its settings changed one at a time, its answers' marks previewed after
each change beside the marking's summary, which components/landing/methods.js plays."""

from collections.abc import Callable
from dataclasses import dataclass
from itertools import islice, pairwise
from typing import Any

from gradeflow_core import BUILTINS, QuestionResult

from gradeflow_app.imports.models import Student
from gradeflow_app.landing.content import Answer, Example, ExampleVariant, landing_content
from gradeflow_app.landing.demos import (
    DRAFT,
    STUDENT_IDS,
    editor_summary,
    marked_submissions,
    preview_results,
    same_answer_groups,
    student_answers,
    unsaved_question,
)
from gradeflow_app.marking.answers.column import answers_column
from gradeflow_app.marking.answers.combinations import Combination, combinations_of
from gradeflow_app.marking.presenters import MarkingSummary
from gradeflow_app.marking.previews import PreviewResults
from gradeflow_app.results.marks import Award, StudentMarks
from gradeflow_app.results.models import Result

type VariantResults = list[dict[str, QuestionResult]]
"""Each answer's result for each question a variant's marking gives marks to."""


@dataclass(frozen=True)
class CombinationsColumn:
    """A question group's answers column, as marking/combinations.html reads it."""

    question_keys: list[str]
    descriptions: dict[str, str]
    """The description of each question that has one, by key."""
    rows: list[Combination]

    @property
    def student_count(self) -> int:
        return sum(row.student_count for row in self.rows)


@dataclass(frozen=True)
class MethodView:
    example_key: str
    label: str
    columns: list[dict[str, Any]] | list[CombinationsColumn]
    """The answers after each variant, the marks of the variant before beside the new ones."""
    summaries: list[MarkingSummary]
    chapters: dict[str, None]
    """Each change to the settings, a chapter without an icon."""

    @property
    def is_question_group(self) -> bool:
        return isinstance(self.columns[0], CombinationsColumn)


def variant_results(example: Example) -> list[VariantResults]:
    """Each variant's results: the core's, or a language model's stored ones."""
    answers = [answer.answers for answer in example.answers]

    def results_of(variant: ExampleVariant) -> VariantResults:
        if variant.results is not None:
            [key] = example.question_set
            return [{key: result} for result in variant.results]
        return [
            dict(submission.results)
            for submission in marked_submissions(example.question_set, variant.marking, answers)
        ]

    return [results_of(variant) for variant in example.variants]


def variant_summaries(example: Example) -> list[MarkingSummary]:
    return [editor_summary(example.question_set, variant.marking) for variant in example.variants]


def answers_columns(example: Example) -> list[dict[str, Any]]:
    """The question's answer groups after each variant."""
    [key] = example.question_set
    question = unsaved_question(example.question_set, key)

    def column(results: VariantResults, previews: VariantResults | None) -> dict[str, Any]:
        groups = same_answer_groups(
            [
                entry
                for answer, result in zip(example.answers, results, strict=True)
                for entry in student_answers(
                    question.core_question,
                    Answer(text=answer.answers[key], student_count=answer.student_count),
                    result[key],
                )
            ]
        )
        preview_by_student = None
        if previews is not None:
            preview_by_student = preview_results(
                groups,
                key,
                {answer.answers[key]: preview[key] for answer, preview in zip(example.answers, previews, strict=True)},
            )
        return answers_column(question, groups, {"group": "answers"}, preview_by_student) | {"draft": DRAFT}

    return variant_columns(example, column)


def combinations_columns(example: Example) -> list[CombinationsColumn]:
    """The question group's combinations of answers after each variant."""
    descriptions = {key: question.description for key, question in example.question_set.items() if question.description}

    def column(results: VariantResults, previews: VariantResults | None) -> CombinationsColumn:
        keys = [key for key in example.question_set if key in results[0]]
        students = []
        preview_by_student: PreviewResults = {}
        for index, (answer, result) in enumerate(zip(example.answers, results, strict=True)):
            awards = {
                key: Award(
                    question.max_marks, Result.from_question_result(result[key], revision=1) if key in result else None
                )
                for key, question in example.question_set.items()
            }
            for student_id in islice(STUDENT_IDS, answer.student_count):
                students.append(StudentMarks(Student(encrypted_id=student_id, answers=answer.answers), awards))
                if previews is not None:
                    preview_by_student[student_id] = previews[index]
        return CombinationsColumn(
            keys, descriptions, combinations_of(example.question_set, keys, students, preview_by_student)
        )

    return variant_columns(example, column)


def variant_columns[C](example: Example, column: Callable[[VariantResults, VariantResults | None], C]) -> list[C]:
    """`column` after each variant: the first variant's results, then each variant's previewed beside the variant
    before's."""
    results = variant_results(example)
    return [
        column(results[0], None),
        *(column(previous, current) for previous, current in pairwise(results)),
    ]


def method_views() -> list[MethodView]:
    views = []
    for key, example in landing_content().examples.items():
        columns = (combinations_columns if len(example.question_set) > 1 else answers_columns)(example)
        views.append(
            MethodView(
                example_key=key,
                label=BUILTINS.find(example.method_ref).label,
                columns=columns,
                summaries=variant_summaries(example),
                chapters=dict.fromkeys(variant.change for variant in example.variants),
            )
        )
    return views
