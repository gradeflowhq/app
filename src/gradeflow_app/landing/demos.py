"""What the landing page's demos share: the app's answers column, shown with made-up students, and their marks and
summaries from the core."""

import uuid
from collections.abc import ItemsView
from dataclasses import dataclass
from itertools import count, islice
from typing import ClassVar

from gradeflow_core import Marked, MarkScheme, QuestionResult, QuestionSet, Submission, mark
from gradeflow_core import Question as CoreQuestion
from gradeflow_core.editing import DECISIONS, Decision, MarkingDraft

from gradeflow_app.jobs.runs import RunState
from gradeflow_app.landing.content import Answer, LanguageModelResult, MarkingJson
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, GroupingOptions, StudentAnswer
from gradeflow_app.marking.answers.column import FILTERS, GROUPINGS, preview_marks
from gradeflow_app.marking.presenters import MarkingSummary
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import PreviewSummary
from gradeflow_app.results.models import Adjustment, Result

ASSESSMENT_ID = uuid.UUID(int=1)
ACCESS = {"can_edit": True, "assessment": {"pk": ASSESSMENT_ID}}
"""The visitor's access: they can edit."""
DRAFT = {"assessment_id": ASSESSMENT_ID, "pk": uuid.UUID(int=2)}
"""The draft the preview bar links to while a marking is edited."""
STUDENT_NUMBERS = count(1)
"""The made-up students' numbers, each used once across the demos."""


@dataclass(frozen=True)
class AnswersColumn:
    """An answers column, as the app's templates read it."""

    question: Question
    grouping: str
    answer_groups: list[AnswerGroup]
    open_answer: str = ""
    """The answer, as the question reads it, whose group is open."""
    draft: dict[str, uuid.UUID] | None = None
    preview_summary: PreviewSummary | None = None
    marking_job: RunState | None = None

    groupings: ClassVar[dict[str, str]] = {name: label for name, (label, _) in GROUPINGS.items()}
    decisions: ClassVar[ItemsView[Decision, str]] = DECISIONS.items()

    @property
    def grouping_options(self) -> GroupingOptions:
        return GROUPINGS[self.grouping][1]

    @property
    def filters(self) -> dict[str, str]:
        """The filters the app offers: "Changed" only while previewing."""
        return {name: label for name, (label, _) in FILTERS.items() if name != "changed" or self.draft}

    @property
    def groups_with_open(self) -> list[tuple[AnswerGroup, bool]]:
        """Each group, with whether it is open."""
        return [(group, group.answer == self.open_answer) for group in self.answer_groups]


def unsaved_question(question_set: QuestionSet, key: str) -> Question:
    return Question(
        assessment_id=ASSESSMENT_ID, key=key, position=0, definition=question_set[key].model_dump(mode="json")
    )


def marked_submissions(
    question_set: QuestionSet, marking: MarkingJson, answers: list[dict[str, str]]
) -> list[Marked[Submission]]:
    """Each student's `answers`, by question, marked with `marking`."""
    submissions = [Submission(student_id=str(index), answers=answer) for index, answer in enumerate(answers)]
    return list(mark(question_set, MarkScheme.load([marking]), submissions))


def results_by_answer(
    question_set: QuestionSet, marking: MarkingJson, answers: list[str], key: str
) -> dict[str, QuestionResult]:
    """Each of `answers` to the question `key`, marked with `marking`."""
    submissions = marked_submissions(question_set, marking, [{key: answer} for answer in answers])
    return {answer: submission.results[key] for answer, submission in zip(answers, submissions, strict=True)}


def language_model_result(result: LanguageModelResult, max_marks: float, method_label: str) -> QuestionResult:
    """A language model's stored `result` as a `QuestionResult`."""
    return QuestionResult(
        proportion=(result.marks or 0) / max_marks,
        max_marks=max_marks,
        feedback=result.feedback,
        method_label=method_label,
        is_marked=result.marks is not None,
    )


def editor_summary(question_set: QuestionSet, marking: MarkingJson) -> MarkingSummary:
    """The summary the editor shows for `marking`."""
    draft = MarkingDraft(marking, question_set, marking.get("question"))
    return MarkingSummary(draft.label(""), draft.describe(""), needs_model_endpoint=draft.needs_model_endpoint(""))


def student_answers(
    question: CoreQuestion, answer: Answer, result: QuestionResult | None, adjustment: Adjustment | None = None
) -> list[StudentAnswer]:
    """A student answer for each student who gave `answer`; with no `result`, not marked yet."""
    normalized = question.normalize(answer.text)
    return [
        StudentAnswer(
            max_marks=question.max_marks,
            result=None if result is None else Result.from_question_result(result, revision=1),
            adjustment=adjustment,
            student_pk=str(number),
            encrypted_student_id=f"enc:v1:{number}",
            answer=answer.text,
            normalized=normalized,
        )
        for number in islice(STUDENT_NUMBERS, answer.student_count)
    ]


def largest_first(groups: list[AnswerGroup]) -> list[AnswerGroup]:
    """The largest groups first, as the answers column orders them."""
    return sorted(groups, key=lambda group: (-group.student_count, group.answer))


def set_previews(groups: list[AnswerGroup], results: dict[str, QuestionResult]) -> None:
    """Gives each group the preview of `results`, by answer."""
    by_student = {
        entry.encrypted_student_id: results[entry.answer] for group in groups for entry in group.student_answers
    }
    for group in groups:
        group.preview = preview_marks(group, by_student)
