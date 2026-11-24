"""The grouping demo: Q1's answers grouped by the same answers, then similar spelling, then similar meaning, and one
group adjusted, which components/landing/groups.js plays."""

from collections.abc import Collection
from dataclasses import dataclass
from typing import Any

from gradeflow_app.landing.content import Groups, landing_content
from gradeflow_app.landing.demos import (
    results_by_answer,
    same_answer_groups,
    student_answers,
    unsaved_question,
)
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, StudentAnswer, group
from gradeflow_app.marking.answers.column import GROUPINGS, answers_column
from gradeflow_app.questions.models import Question
from gradeflow_app.results.models import Adjustment

CHAPTERS = {
    "Same answers": "queue-list",
    "Similar spelling": "language",
    "Similar meaning": "chat-bubble-left-right",
    "Adjust marks": "pencil",
}
"""The chapters the demo plays, with their icons."""


@dataclass(frozen=True)
class Grouping:
    column: dict[str, Any]
    next_group_indexes: list[int]
    """For each group, the index of the group it joins in the next grouping; empty when no regrouping follows."""


@dataclass(frozen=True)
class GroupsView:
    content: Groups
    question: Question
    groupings: list[Grouping]
    """By the same answers, similar spelling and similar meaning, then with one group adjusted."""
    adjusted_group_index: int
    adjusted_count: int

    chapters = CHAPTERS


def next_group_indexes(groups: list[AnswerGroup], next_groups: list[AnswerGroup]) -> list[int]:
    """For each of `groups`, the index of the group of `next_groups` it joins."""
    group_index_by_answer = {
        entry.answer: index for index, group in enumerate(next_groups) for entry in group.student_answers
    }
    return [group_index_by_answer[group.student_answers[0].answer] for group in groups]


def groups_view() -> GroupsView:
    content = landing_content().groups
    question = unsaved_question(content.question_set, content.question_key)
    answer_results = results_by_answer(
        content.question_set, content.marking, [answer.text for answer in content.answers], content.question_key
    )
    adjustment = Adjustment(marks=content.adjustment.marks, feedback=content.adjustment.feedback)
    meaning = GROUPINGS["meaning"][1]

    def students(adjusted: Collection[str] = ()) -> list[StudentAnswer]:
        """Every student's answer, adjusted for the answers in `adjusted`."""
        return [
            entry
            for answer in content.answers
            for entry in student_answers(
                question.core_question,
                answer,
                answer_results[answer.text],
                adjustment if answer.text in adjusted else None,
            )
        ]

    unadjusted = students()
    answer_groups = same_answer_groups(unadjusted)
    spelling_groups = group(unadjusted, GROUPINGS["spelling"][1])
    meaning_groups = group(unadjusted, meaning, content.meaning_comparison)
    [adjusted_answers] = [
        dict(meaning_group.answers)
        for meaning_group in meaning_groups
        if content.adjustment.answer in dict(meaning_group.answers)
    ]
    adjusted_groups = group(students(adjusted_answers), meaning, content.meaning_comparison)
    [adjusted_group_index] = [index for index, group in enumerate(adjusted_groups) if group.adjusted_count]
    return GroupsView(
        content=content,
        question=question,
        groupings=[
            Grouping(
                answers_column(question, answer_groups, {"group": "answers"}, None),
                next_group_indexes(answer_groups, spelling_groups),
            ),
            Grouping(
                answers_column(question, spelling_groups, {"group": "spelling"}, None),
                next_group_indexes(spelling_groups, meaning_groups),
            ),
            Grouping(answers_column(question, meaning_groups, {"group": "meaning"}, None), []),
            Grouping(answers_column(question, adjusted_groups, {"group": "meaning"}, None), []),
        ],
        adjusted_group_index=adjusted_group_index,
        adjusted_count=adjusted_groups[adjusted_group_index].adjusted_count,
    )
