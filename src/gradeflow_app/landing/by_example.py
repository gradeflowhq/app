"""The marking-by-example demo: answers given full marks, partial marks and none as not allowed, the marking's summary
changing with each, which components/landing/by-example.js plays."""

from dataclasses import dataclass

from gradeflow_core.editing import Decision

from gradeflow_app.landing.content import CoreByExampleStage, core_output, landing_content
from gradeflow_app.landing.demos import (
    DRAFT,
    AnswersColumn,
    largest_first,
    set_previews,
    student_answers,
    unsaved_question,
)
from gradeflow_app.marking.answers.answer_groups import AnswerGroup
from gradeflow_app.marking.answers.column import preview_summary, set_decisions
from gradeflow_app.marking.presenters import MarkingSummary
from gradeflow_app.questions.models import Question

CHAPTERS = {"Full marks": "check-circle", "Partial marks": "minus-circle", "Not allowed": "no-symbol"}
"""The chapters the demo plays, one for each decision, with their icons."""


@dataclass(frozen=True)
class ByExampleView:
    question: Question
    columns: list[AnswersColumn]
    """The answers in the draft before marking by example, then previewed after each decision, the answer decided
    open."""
    summaries: list[MarkingSummary]
    decision_group_indexes: list[int]
    """The index of the group each decision is for."""
    decisions: list[Decision]

    chapters = CHAPTERS


def by_example_view() -> ByExampleView:
    content, core = landing_content().by_example, core_output().by_example
    question = unsaved_question(content.question_set, content.question_key)
    max_marks = question.core_question.max_marks
    initial_stage = core.stages[0]

    def column(stage: CoreByExampleStage, decided: str | None) -> AnswersColumn:
        """The answers with the decisions of `stage`, previewed after the decision for the answer `decided`, its group
        open."""
        groups = largest_first(
            [
                AnswerGroup(
                    student_answers(
                        max_marks, answer, core.normalized_answers[answer.text], initial_stage.results[answer.text]
                    )
                )
                for answer in content.answers
            ]
        )
        set_decisions(groups, stage.decisions)
        if decided is None:
            return AnswersColumn(question, "answers", groups, draft=DRAFT)
        set_previews(groups, stage.results)
        return AnswersColumn(
            question,
            "answers",
            groups,
            core.normalized_answers[decided],
            draft=DRAFT,
            preview_summary=preview_summary(groups),
        )

    columns = [column(stage, decided) for stage, decided in zip(core.stages, [None, *content.decisions], strict=True)]
    first_column_answers = [group.answer for group in columns[0].answer_groups]
    return ByExampleView(
        question=question,
        columns=columns,
        summaries=[stage.summary for stage in core.stages],
        decision_group_indexes=[
            first_column_answers.index(core.normalized_answers[answer]) for answer in content.decisions
        ],
        decisions=list(content.decisions.values()),
    )
