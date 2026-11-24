"""The marking-by-example demo: answers given full marks, partial marks and none as not allowed, the marking's summary
changing with each, which components/landing/by-example.js plays."""

from dataclasses import dataclass

from gradeflow_core import QuestionResult
from gradeflow_core.editing import Decision, MarkingDraft

from gradeflow_app.landing.content import landing_content
from gradeflow_app.landing.demos import (
    DRAFT,
    AnswersColumn,
    editor_summary,
    largest_first,
    results_by_answer,
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


@dataclass(frozen=True)
class Stage:
    """The marking after a decision, or before the first."""

    results: dict[str, QuestionResult]
    """By answer."""
    summary: MarkingSummary
    decisions: dict[str, Decision]
    """The decisions so far, by answer as the question reads it."""


def by_example_view() -> ByExampleView:
    content = landing_content().by_example
    question = unsaved_question(content.question_set, content.question_key)
    normalize = question.core_question.normalize
    answers = [answer.text for answer in content.answers]
    draft = MarkingDraft(content.marking, content.question_set, content.question_key)

    def stage() -> Stage:
        return Stage(
            results_by_answer(content.question_set, draft.data, answers, content.question_key),
            editor_summary(content.question_set, draft.data),
            draft.answer_decisions(),
        )

    stages = [stage()]
    for answer, decision in content.decisions.items():
        draft.mark_by_example([answer], decision)
        stages.append(stage())

    def column(stage: Stage, decided: str | None) -> AnswersColumn:
        """The answers with the decisions of `stage`, previewed after the decision for the answer `decided`, its group
        open."""
        groups = largest_first(
            [
                AnswerGroup(student_answers(question.core_question, answer, stages[0].results[answer.text]))
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
            normalize(decided),
            draft=DRAFT,
            preview_summary=preview_summary(groups),
        )

    columns = [column(stage, decided) for stage, decided in zip(stages, [None, *content.decisions], strict=True)]
    first_column_answers = [group.answer for group in columns[0].answer_groups]
    return ByExampleView(
        question=question,
        columns=columns,
        summaries=[stage.summary for stage in stages],
        decision_group_indexes=[first_column_answers.index(normalize(answer)) for answer in content.decisions],
        decisions=list(content.decisions.values()),
    )
