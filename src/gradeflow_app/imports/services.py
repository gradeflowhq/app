import re
from dataclasses import dataclass

from django.db import transaction
from gradeflow_core import AnswersCsv, GradeFlowError, QuestionSet, read_answer_key, read_question_document

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.imports.models import Student, SubmissionsFile
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.questions.models import Question

ENCRYPTED_ID = re.compile(r"^enc:v1:[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+$")
ENCRYPTED_FILE_HEADER = b"GFF\x01"
ENCRYPTED_FILE_OVERHEAD = 32
"""A 16-byte header and a 16-byte tag; see crypto.js."""


class ImportFailed(ValueError):
    pass


@dataclass
class ImportSummary:
    student_count: int
    question_count: int
    added_questions: list[str]
    removed_questions: list[str]
    new_students: int
    removed_students: int
    changed_students: int
    first_import: bool
    changed_questions: set[str]
    """The questions whose answers or settings changed, which marking reading them has to mark again."""


@transaction.atomic
def import_submissions(
    assessment: Assessment,
    user: User,
    file: SubmissionsFile,
    csv_text: str,
    key_row: dict[str, str] | None = None,
) -> ImportSummary:
    """Replace the assessment's students and answers with `csv_text`, a CSV whose student IDs are already encrypted.

    Questions already set up keep their settings but accept the new answers, such as a choice's new options; new ones
    are worked out from the answers. Both read the answers and `key_row`, the file's answer key row by answer column.
    `file` is unsaved and holds the choices made reading the file, so they can be changed later, its `original`, the
    whole file encrypted in the browser, and any ID mapping file, encrypted too.
    """
    if not all(map(_is_absent_or_encrypted, (file.original, file.id_mapping_file))):
        raise ImportFailed("Files must be encrypted in the browser before they're uploaded.")
    csv_format = AnswersCsv(
        student_id_column=file.student_id_column,
        answer_columns=file.answer_columns,
        marks_columns=file.marks_columns,
    )
    try:
        submissions = csv_format.read(csv_text)
    except GradeFlowError as error:
        raise ImportFailed(str(error)) from error
    if not submissions:
        raise ImportFailed("The file has no students.")
    if any(not ENCRYPTED_ID.match(submission.student_id) for submission in submissions):
        raise ImportFailed("Student IDs must be encrypted in the browser before they're uploaded.")

    assessment.lock()
    existing = {student.encrypted_id: student for student in assessment.students.all()}
    incoming = {submission.student_id: submission for submission in submissions}
    removed_keys = existing.keys() - incoming.keys()
    changed_by_student = {
        key: {
            question
            for question in existing[key].answers.keys() | incoming[key].answers.keys()
            if existing[key].answers.get(question, "") != incoming[key].answers.get(question, "")
        }
        for key in existing.keys() & incoming.keys()
    }
    # A question one file has and the other doesn't is no change to anyone's answers.
    changed_students = sum(
        1
        for key, questions in changed_by_student.items()
        if questions & existing[key].answers.keys() & incoming[key].answers.keys()
    )
    previous_questions = {question.key: question.definition for question in assessment.questions.all()}
    first_import = not existing and not previous_questions
    updated_questions = assessment.question_set().updated_from([*submissions, read_answer_key(key_row or {})])

    assessment.students.filter(encrypted_id__in=removed_keys).delete()
    Student.objects.bulk_create(
        [
            Student(
                assessment=assessment,
                encrypted_id=key,
                answers=submission.answers,
                imported_marks=submission.marks,
            )
            for key, submission in incoming.items()
        ],
        update_conflicts=True,
        unique_fields=["assessment", "encrypted_id"],
        update_fields=["answers", "imported_marks"],
    )
    _store_questions(assessment, updated_questions, file.answer_columns)
    file.assessment = assessment
    file.uploaded_by = user
    file.student_count = len(incoming)
    file.save()
    assessment.submissions_files.exclude(pk=file.pk).update(original=None, id_mapping_file=None)
    assessment.save(update_fields=["updated_at"])
    notify_change(topic("questions", assessment.pk))

    previous_keys = previous_questions.keys()
    changed_questions = set().union(*changed_by_student.values())
    changed_questions.update(
        question.key
        for question in assessment.questions.all()
        if question.definition != previous_questions.get(question.key)
    )
    changed_questions.update(previous_keys - updated_questions.keys())
    if existing.keys() != incoming.keys():
        # A marking marks every student at once, so new or removed students mean marking every question again.
        changed_questions.update(previous_keys | updated_questions.keys())
    return ImportSummary(
        student_count=len(incoming),
        question_count=len(updated_questions),
        added_questions=sorted(updated_questions.keys() - previous_keys),
        removed_questions=sorted(previous_keys - updated_questions.keys()),
        new_students=len(incoming.keys() - existing.keys()),
        removed_students=len(removed_keys),
        changed_students=changed_students,
        first_import=first_import,
        changed_questions=changed_questions,
    )


def _is_absent_or_encrypted(data: bytes | memoryview | None) -> bool:
    return data is None or (
        data[: len(ENCRYPTED_FILE_HEADER)] == ENCRYPTED_FILE_HEADER and len(data) >= ENCRYPTED_FILE_OVERHEAD
    )


def _store_questions(assessment: Assessment, questions: QuestionSet, column_order: list[str]) -> None:
    """Keeps the questions the file answers, in file order, as `questions` sets them up."""
    positions = {key: index for index, key in enumerate(column_order)}
    assessment.questions.exclude(key__in=questions).delete()
    current = {question.key: question for question in assessment.questions.all()}
    for key, core_question in questions.items():
        question = current.get(key) or Question(assessment=assessment, key=key)
        question.core_question = core_question
        question.position = positions.get(key, len(positions))
        question.save()


@transaction.atomic
def apply_question_document(assessment: Assessment, data: bytes) -> tuple[list[str], set[str]]:
    """Sets up the questions an ExamSoft question document has as it shows them, replacing their settings; returns
    the questions it has and those whose settings changed."""
    assessment.lock()
    try:
        document_questions = read_question_document(data, assessment.question_set())
    except GradeFlowError as error:
        raise ImportFailed(str(error)) from error
    if not document_questions:
        raise ImportFailed("The question document has none of the submissions file's questions.")
    changed = set()
    for question in assessment.questions.filter(key__in=list(document_questions)):
        if question.core_question != document_questions[question.key]:
            question.core_question = document_questions[question.key]
            question.save()
            changed.add(question.key)
    notify_change(topic("questions", assessment.pk))
    return sorted(document_questions), changed
