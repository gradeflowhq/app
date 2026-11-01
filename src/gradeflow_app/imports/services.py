import re
from dataclasses import dataclass

from django.db import transaction
from gradeflow_core import AnswersCsv, GradeFlowError, QuestionSet, read_answer_key, read_question_document

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.imports.models import ResultsFile, Student
from gradeflow_app.live.topics import publish, topic
from gradeflow_app.questions.models import Question

ENVELOPE = re.compile(r"^enc:v2:[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")
ENCRYPTED_FILE = b"GFF\x01"
ENCRYPTED_FILE_OVERHEAD = 48
"""A 32-byte header and a 16-byte tag; see crypto.js."""


class ImportFailed(ValueError):
    pass


@dataclass
class Summary:
    students: int
    questions: int
    added: list[str]
    removed: list[str]
    new_students: int
    removed_students: int
    changed: int
    first_import: bool
    changed_questions: set[str]
    """The questions whose answers or settings changed, which marking reading them has to mark again."""


@transaction.atomic
def import_results(
    assessment: Assessment,
    user: User,
    *,
    name: str,
    data: str,
    student_id_column: str,
    answer_columns: list[str],
    marks_columns: dict[str, str],
    key_row: dict[str, str] | None = None,
    original: bytes | None = None,
    sheet: str = "",
    header_row: int | None = None,
    first_row: int | None = None,
    last_row: int | None = None,
) -> Summary:
    """Replace the assessment's results with `data`, a CSV whose student IDs are already encrypted.

    Questions already set up keep their settings but accept the new answers, such as a choice's new options; new ones
    are worked out from the answers. Both read the answers and `key_row`, the file's answer key row by answer column.
    `original` is the whole file, encrypted in the browser, and the other arguments are the choices made reading it,
    so they can be changed later.
    """
    if original is not None and not (original.startswith(ENCRYPTED_FILE) and len(original) >= ENCRYPTED_FILE_OVERHEAD):
        raise ImportFailed("The original file must be encrypted in the browser before it's uploaded.")
    config = AnswersCsv(student_id_column=student_id_column, answer_columns=answer_columns, marks_columns=marks_columns)
    try:
        raw = config.read(data)
    except GradeFlowError as error:
        raise ImportFailed(str(error)) from error
    if not raw:
        raise ImportFailed("The file has no students.")
    if any(not ENVELOPE.match(submission.student_id) for submission in raw):
        raise ImportFailed("Student IDs must be encrypted in the browser before they're uploaded.")

    assessment.lock()
    existing = {student.key: student for student in assessment.students.all()}
    previous_questions = {question.key: question.definition for question in assessment.questions.all()}
    first_import = not existing and not assessment.questions.exists()
    incoming = {submission.student_id: submission for submission in raw}
    differing = {
        key: {
            question
            for question in existing[key].answers.keys() | incoming[key].answers.keys()
            if existing[key].answers.get(question, "") != incoming[key].answers.get(question, "")
        }
        for key in existing.keys() & incoming.keys()
    }
    changed_inputs = set().union(*differing.values())
    gone = existing.keys() - incoming.keys()
    assessment.students.filter(key__in=gone).delete()
    # A question one file has and the other doesn't is no change to anyone's answers.
    changed = sum(
        1
        for key, questions in differing.items()
        if questions & existing[key].answers.keys() & incoming[key].answers.keys()
    )
    Student.objects.bulk_create(
        [
            Student(
                assessment=assessment,
                key=key,
                answers=submission.answers,
                file_marks=submission.marks,
            )
            for key, submission in incoming.items()
        ],
        update_conflicts=True,
        unique_fields=["assessment", "key"],
        update_fields=["answers", "file_marks"],
    )

    questions = assessment.question_set()
    before = set(questions)
    answered = questions.updated_from([*raw, read_answer_key(key_row or {})])
    _store_questions(assessment, answered, answer_columns)
    file = ResultsFile.objects.create(
        assessment=assessment,
        name=name,
        uploaded_by=user,
        student_id_column=student_id_column,
        answer_columns=answer_columns,
        marks_columns=marks_columns,
        student_count=len(incoming),
        original=original,
        sheet=sheet,
        header_row=header_row,
        first_row=first_row,
        last_row=last_row,
    )
    assessment.results_files.exclude(pk=file.pk).filter(original__isnull=False).update(original=None)
    assessment.save(update_fields=["updated_at"])
    changed_inputs.update(
        question.key
        for question in assessment.questions.all()
        if question.definition != previous_questions.get(question.key)
    )
    changed_inputs.update(before - answered.keys())
    if existing.keys() != incoming.keys():
        changed_inputs.update(before | answered.keys())
    publish(topic("questions", assessment.pk))
    return Summary(
        students=len(incoming),
        questions=len(answered),
        added=sorted(answered.keys() - before),
        removed=sorted(before - answered.keys()),
        new_students=len(incoming.keys() - existing.keys()),
        removed_students=len(gone),
        changed=changed,
        first_import=first_import,
        changed_questions=changed_inputs,
    )


def _store_questions(assessment: Assessment, answered: QuestionSet, order: list[str]) -> None:
    """Keeps the questions the file answers, in file order, as `answered` sets them up."""
    position = {key: index for index, key in enumerate(order)}
    assessment.questions.exclude(key__in=answered).delete()
    current = {question.key: question for question in assessment.questions.all()}
    for key, core_question in answered.items():
        question = current.get(key) or Question(assessment=assessment, key=key)
        question.core = core_question
        question.position = position.get(key, len(position))
        question.save()


@transaction.atomic
def apply_question_document(assessment: Assessment, data: bytes) -> tuple[list[str], set[str]]:
    """Sets up the questions an ExamSoft question document has as it shows them, replacing their settings; returns
    the questions it has and those whose settings changed."""
    assessment.lock()
    try:
        described = read_question_document(data, assessment.question_set())
    except GradeFlowError as error:
        raise ImportFailed(str(error)) from error
    if not described:
        raise ImportFailed("The question document has none of the exam export's questions.")
    changed = set()
    for question in assessment.questions.filter(key__in=list(described)):
        if question.core != described[question.key]:
            question.core = described[question.key]
            question.save()
            changed.add(question.key)
    publish(topic("questions", assessment.pk))
    return sorted(described), changed
