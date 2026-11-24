"""What the landing page's demos share: made-up students, and their marks and summaries from the core."""

import uuid
from collections.abc import Iterator
from itertools import count, islice

from gradeflow_core import Marked, MarkScheme, QuestionResult, QuestionSet, Submission, mark
from gradeflow_core import Question as CoreQuestion
from gradeflow_core.editing import MarkingDraft

from gradeflow_app.landing.content import Answer, MarkingJson
from gradeflow_app.marking.answers.answer_groups import AnswerGroup, StudentAnswer, group
from gradeflow_app.marking.answers.column import GROUPINGS
from gradeflow_app.marking.presenters import MarkingSummary
from gradeflow_app.marking.previews import PreviewResults
from gradeflow_app.questions.models import Question
from gradeflow_app.results.models import Adjustment, Result

ASSESSMENT_ID = uuid.UUID(int=1)
ACCESS = {"can_edit": True, "assessment": {"pk": ASSESSMENT_ID}}
"""The visitor's access: they can edit."""
DRAFT = {"assessment_id": ASSESSMENT_ID, "pk": uuid.UUID(int=2)}
"""The draft the preview bar links to while a marking is edited."""
STUDENT_IDS = (f"{number:08}" for number in count(1))
"""The made-up students' IDs, each used once across the demos."""


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


def editor_summary(question_set: QuestionSet, marking: MarkingJson) -> MarkingSummary:
    """The summary the editor shows for `marking`."""
    draft = MarkingDraft(marking, question_set, marking.get("question"))
    return MarkingSummary(draft.label(""), draft.describe(""), needs_model_endpoint=draft.needs_model_endpoint(""))


def student_answers(
    question: CoreQuestion,
    answer: Answer,
    result: QuestionResult | None,
    adjustment: Adjustment | None = None,
    student_ids: Iterator[str] = STUDENT_IDS,
) -> list[StudentAnswer]:
    """A student answer for each student who gave `answer`, each with an ID from `student_ids`; with no `result`, not
    marked yet."""
    normalized = question.normalize(answer.text)
    return [
        StudentAnswer(
            max_marks=question.max_marks,
            result=None if result is None else Result.from_question_result(result, revision=1),
            adjustment=adjustment,
            student_pk=str(uuid.uuid5(uuid.NAMESPACE_URL, student_id)),
            encrypted_student_id=student_id,
            answer=answer.text,
            normalized=normalized,
        )
        for student_id in islice(student_ids, answer.student_count)
    ]


def same_answer_groups(student_answers: list[StudentAnswer]) -> list[AnswerGroup]:
    return group(student_answers, GROUPINGS["answers"][1])


def preview_results(groups: list[AnswerGroup], key: str, results: dict[str, QuestionResult]) -> PreviewResults:
    """Each student in `groups`, with the result in `results` for their answer to the question `key`."""
    return {
        entry.encrypted_student_id: {key: results[entry.answer]} for group in groups for entry in group.student_answers
    }
