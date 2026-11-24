"""The trust demo: the students of an answer group sharing its mark, an answer's breakdown, and a question marked by
rules, then by a method that asks the AI service, which components/landing/trust.js plays beside the claims."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from gradeflow_app.landing.content import TrustQuestion, landing_content
from gradeflow_app.landing.demos import (
    STUDENT_IDS,
    results_by_answer,
    same_answer_groups,
    student_answers,
    unsaved_question,
)
from gradeflow_app.landing.methods import answers_columns, variant_summaries
from gradeflow_app.marking.answers.column import answers_column
from gradeflow_app.marking.presenters import MarkingSummary

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
    same_mark: dict[str, Any]
    breakdown: dict[str, Any]
    opened_group_index: int
    """The index of the group of `breakdown` that the demo opens."""
    ai_columns: list[dict[str, Any]]
    """The answers marked by rules, then previewed with the method that asks the AI service."""
    ai_summaries: list[MarkingSummary]

    chapters = CLAIMS


def marked_column(content: TrustQuestion, student_ids: Iterator[str] = STUDENT_IDS) -> dict[str, Any]:
    """The answers to `content`'s question, marked, each student with an ID from `student_ids`."""
    question = unsaved_question(content.question_set, content.question_key)
    answers = [answer.text for answer in content.answers]
    answer_results = results_by_answer(content.question_set, content.marking, answers, content.question_key)
    entries = [
        entry
        for answer in content.answers
        for entry in student_answers(
            question.core_question, answer, answer_results[answer.text], student_ids=student_ids
        )
    ]
    return answers_column(question, same_answer_groups(entries), {"group": "answers"}, None)


def trust_view() -> TrustView:
    content = landing_content().trust
    breakdown = marked_column(content.breakdown)
    opened = breakdown["question"].core_question.normalize(content.opened_answer)
    return TrustView(
        same_mark=marked_column(content.same_mark, iter(content.student_names)),
        breakdown=breakdown,
        opened_group_index=[group.answer for group in breakdown["answer_groups"]].index(opened),
        ai_columns=answers_columns(content.ai),
        ai_summaries=variant_summaries(content.ai),
    )
