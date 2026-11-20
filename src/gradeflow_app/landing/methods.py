"""The method demos: an example of each method, its settings changed one at a time, its answers' marks previewed after
each change beside the marking's summary, which components/landing/methods.js plays."""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, replace
from itertools import pairwise

from gradeflow_core import QuestionResult

from gradeflow_app.landing.content import Answer, CoreExample, Example, core_output, landing_content
from gradeflow_app.landing.demos import (
    DRAFT,
    AnswersColumn,
    largest_first,
    set_previews,
    student_answers,
    unsaved_question,
)
from gradeflow_app.marking.answers.answer_groups import AnswerGroup
from gradeflow_app.marking.answers.combinations import Cell, Combination
from gradeflow_app.marking.presenters import MarkingSummary
from gradeflow_app.results.marks import Award
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
    columns: list[AnswersColumn] | list[CombinationsColumn]
    """The answers after each variant, the marks of the variant before beside the new ones."""
    summaries: list[MarkingSummary]
    chapters: dict[str, None]
    """Each change to the settings, a chapter without an icon."""

    @property
    def is_question_group(self) -> bool:
        return isinstance(self.columns[0], CombinationsColumn)


def answers_columns(example: Example, core: CoreExample) -> list[AnswersColumn]:
    """The question's answer groups after each variant."""
    [key] = example.question_set
    question = unsaved_question(example.question_set, key)
    max_marks = question.core_question.max_marks

    def column(results: VariantResults, previews: VariantResults | None) -> AnswersColumn:
        groups = largest_first(
            [
                AnswerGroup(
                    student_answers(
                        max_marks,
                        Answer(text=answer.answers[key], student_count=answer.student_count),
                        normalized[key],
                        result[key],
                    )
                )
                for answer, normalized, result in zip(example.answers, core.normalized_answers, results, strict=True)
            ]
        )
        if previews is not None:
            set_previews(
                groups,
                {answer.answers[key]: preview[key] for answer, preview in zip(example.answers, previews, strict=True)},
            )
        return AnswersColumn(question, "answers", groups, draft=DRAFT)

    return variant_columns(core, column)


def combinations_columns(example: Example, core: CoreExample) -> list[CombinationsColumn]:
    """The question group's combinations of answers after each variant."""

    def combination_cell(
        key: str, answer: str, count: int, result: QuestionResult | None, preview: QuestionResult | None
    ) -> Cell:
        question = example.question_set[key]
        if result is None:
            return Cell(key, answer, question, read=True)
        award = Award(question.max_marks, result=Result.from_question_result(result, revision=1))
        cell = Cell(key, answer, question, marks={award.marks}, feedback_counts=Counter({award.feedback: count}))
        if preview is not None:
            preview_award = replace(award, preview_result=preview)
            assert preview_award.marks is not None
            cell.preview_marks.add(preview_award.marks)
            cell.preview_feedback_counts[preview_award.feedback] = count
            cell.changed_count = count if award.differs_from(preview_award) else 0
        return cell

    def column(results: VariantResults, previews: VariantResults | None) -> CombinationsColumn:
        rows = []
        for index, (answer, normalized, result) in enumerate(
            zip(example.answers, core.normalized_answers, results, strict=True)
        ):
            preview = None if previews is None else previews[index]
            cells = [
                combination_cell(
                    key,
                    normalized[key],
                    answer.student_count,
                    result.get(key),
                    None if preview is None else preview.get(key),
                )
                for key in example.question_set
            ]
            changed = any(cell.changed for cell in cells)
            rows.append(Combination(cells, answer.student_count, answer.student_count if changed else 0))
        rows.sort(key=lambda row: -row.student_count)
        return CombinationsColumn(
            [key for key in example.question_set if key in results[0]],
            {key: question.description for key, question in example.question_set.items() if question.description},
            rows,
        )

    return variant_columns(core, column)


def variant_columns[C](core: CoreExample, column: Callable[[VariantResults, VariantResults | None], C]) -> list[C]:
    """`column` after each variant: the first variant's results, then each variant's previewed beside the variant
    before's."""
    variant_results = [variant.results for variant in core.variants]
    return [
        column(variant_results[0], None),
        *(column(previous_results, results) for previous_results, results in pairwise(variant_results)),
    ]


def method_views() -> list[MethodView]:
    views = []
    for key, example in landing_content().examples.items():
        core = core_output().examples[key]
        columns = (combinations_columns if len(example.question_set) > 1 else answers_columns)(example, core)
        views.append(
            MethodView(
                example_key=key,
                label=core.method_label,
                columns=columns,
                summaries=[variant.summary for variant in core.variants],
                chapters=dict.fromkeys(variant.change for variant in example.variants),
            )
        )
    return views
