"""The grouping demo: Q1's answers grouped by the same answers, then similar spelling, then similar meaning, and one
group adjusted, which components/landing/groups.js plays."""

from dataclasses import dataclass

from gradeflow_app.landing.content import AnswerWithMeaningGroup, Groups, core_output, landing_content
from gradeflow_app.landing.demos import AnswersColumn, largest_first, student_answers, unsaved_question
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, StudentAnswer
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
    column: AnswersColumn
    next_group_indexes: list[int]
    """For each group, the index of the group it joins in the next grouping; empty when no regrouping follows."""


@dataclass(frozen=True)
class GroupsView:
    content: Groups
    question: Question
    groupings: list[Grouping]
    """By the same answers, similar spelling and similar meaning, then with one group adjusted."""
    adjusted_group_index: int

    chapters = CHAPTERS

    @property
    def adjusted_count(self) -> int:
        return self.groupings[-1].column.answer_groups[self.adjusted_group_index].adjusted_count


def next_group_indexes(groups: list[AnswerGroup], next_groups: list[AnswerGroup]) -> list[int]:
    """For each of `groups`, the index of the group of `next_groups` it joins."""
    group_index_by_answer = {
        entry.answer: index for index, group in enumerate(next_groups) for entry in group.student_answers
    }
    return [group_index_by_answer[group.student_answers[0].answer] for group in groups]


def groups_view() -> GroupsView:
    content, core = landing_content().groups, core_output().groups
    question = unsaved_question(content.question_set, content.question_key)
    max_marks = question.core_question.max_marks
    answers = {answer.text: answer for answer in content.answers}
    adjusted_meaning_group = answers[content.adjustment.answer].meaning_group
    adjustment = Adjustment(marks=content.adjustment.marks, feedback=content.adjustment.feedback)

    def student_answers_for(answer: AnswerWithMeaningGroup, *, adjusted: bool = False) -> list[StudentAnswer]:
        is_adjusted = adjusted and answer.meaning_group == adjusted_meaning_group
        return student_answers(
            max_marks,
            answer,
            core.normalized_answers[answer.text],
            core.results[answer.text],
            adjustment if is_adjusted else None,
        )

    def answer_groups(answer_texts_by_group: list[list[str]], *, adjusted: bool = False) -> list[AnswerGroup]:
        """A group of the answers in each of `answer_texts_by_group`, different answers joined by similarity."""
        groups = []
        for texts in answer_texts_by_group:
            group_student_answers = [
                entry for text in texts for entry in student_answers_for(answers[text], adjusted=adjusted)
            ]
            joined_by_similarity = len({entry.normalized for entry in group_student_answers}) > 1
            groups.append(AnswerGroup(group_student_answers, joined_by_similarity=joined_by_similarity))
        return largest_first(groups)

    answers_by_meaning_group: dict[str, list[str]] = {}
    for answer in content.answers:
        answers_by_meaning_group.setdefault(answer.meaning_group, []).append(answer.text)
    same_answer_groups = largest_first([AnswerGroup(student_answers_for(answer)) for answer in content.answers])
    spelling_groups = answer_groups(core.spelling_groups)
    meaning_groups = answer_groups(list(answers_by_meaning_group.values()))
    adjusted_groups = answer_groups(list(answers_by_meaning_group.values()), adjusted=True)
    [adjusted_group_index] = [index for index, group in enumerate(adjusted_groups) if group.adjusted_count]
    return GroupsView(
        content=content,
        question=question,
        groupings=[
            Grouping(
                AnswersColumn(question, "answers", same_answer_groups),
                next_group_indexes(same_answer_groups, spelling_groups),
            ),
            Grouping(
                AnswersColumn(question, "spelling", spelling_groups),
                next_group_indexes(spelling_groups, meaning_groups),
            ),
            Grouping(AnswersColumn(question, "meaning", meaning_groups), []),
            Grouping(AnswersColumn(question, "meaning", adjusted_groups), []),
        ],
        adjusted_group_index=adjusted_group_index,
    )
