"""The trust demo: the students of an answer group sharing its mark, an answer's breakdown, and a question marked by
rules, then by a method that asks the AI service, which components/landing/trust.js plays beside the claims."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import count, islice

from gradeflow_app.landing.content import CoreTrustQuestion, TrustQuestion, core_output, landing_content
from gradeflow_app.landing.demos import AnswersColumn, largest_first, unsaved_question
from gradeflow_app.landing.methods import answers_columns
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, StudentAnswer
from gradeflow_app.marking.presenters import MarkingSummary
from gradeflow_app.results.models import Result

CLAIMS = {
    "Same answer, same mark": ("equals", "Students who wrote the same answer are marked once and share the mark."),
    "Every mark shows why": (
        "document-magnifying-glass",
        "Each answer lists the checks that gave its marks, and the feedback students get.",
    ),
    "AI only where you add it": (
        "sparkles",
        "Rules mark what they can. Add AI for what they miss, and check every mark it changes.",
    ),
}
"""The claims, each a chapter the demo plays: its icon and description."""


@dataclass(frozen=True)
class TrustView:
    same_mark: AnswersColumn
    breakdown: AnswersColumn
    opened_group_index: int
    """The index of the group of `breakdown` that the demo opens."""
    ai_columns: list[AnswersColumn]
    """The answers marked by rules, then previewed with the method that asks the AI service."""
    ai_summaries: list[MarkingSummary]

    chapters = CLAIMS


def answers_column(content: TrustQuestion, core: CoreTrustQuestion, student_ids: Iterator[str]) -> AnswersColumn:
    """The answers to `content`'s question, marked, each student with an ID from `student_ids`."""
    question = unsaved_question(content.question_set, content.question_key)
    max_marks = question.core_question.max_marks
    groups = [
        AnswerGroup(
            [
                StudentAnswer(
                    max_marks=max_marks,
                    result=Result.from_question_result(core.results[answer.text], revision=1),
                    adjustment=None,
                    student_pk=str(uuid.uuid5(uuid.NAMESPACE_URL, student_id)),
                    encrypted_student_id=student_id,
                    answer=answer.text,
                    normalized=core.normalized_answers[answer.text],
                )
                for student_id in islice(student_ids, answer.student_count)
            ]
        )
        for answer in content.answers
    ]
    return AnswersColumn(question, "answers", largest_first(groups))


def trust_view() -> TrustView:
    content, core = landing_content().trust, core_output().trust
    breakdown = answers_column(content.breakdown, core.breakdown, map(str, count(1)))
    opened = core.breakdown.normalized_answers[content.opened_answer]
    return TrustView(
        same_mark=answers_column(content.same_mark, core.same_mark, iter(content.student_names)),
        breakdown=breakdown,
        opened_group_index=[group.answer for group in breakdown.answer_groups].index(opened),
        ai_columns=answers_columns(content.ai, core.ai),
        ai_summaries=[variant.summary for variant in core.ai.variants],
    )
