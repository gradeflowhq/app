import base64
import json
from pathlib import Path

import pytest
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils.html import escape
from gradeflow_core import AnswersCsv, GradeFlowError, QuestionSet, read_answer_key, read_question_document
from pytest_django import DjangoAssertNumQueries

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.imports import forms
from gradeflow_app.imports.models import SubmissionsFile, imported_mark_keys
from gradeflow_app.imports.services import apply_question_document, import_submissions
from gradeflow_app.marking.models import Marking
from gradeflow_app.results.marks import assessment_results
from gradeflow_app.results.models import Adjustment
from tests.conftest import (
    EXAM_CSV,
    EXAM_ROWS,
    HTMX_HEADERS,
    create_assessment,
    encrypted_id,
    full_marks_marking,
    upload,
)

pytestmark = pytest.mark.django_db

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "encrypted.json").read_text())
"""What crypto.js writes, which tests/js/crypto.test.mjs checks it still does."""
ENCRYPTED_FILE = base64.b64decode(GOLDEN["encrypted_file"])
FILE_SETTINGS = {"sheet": "Results", "header_row": "2", "first_row": "3", "last_row": "5"}


def two_marks_columns_csv(*students: tuple[str, str, str, str]) -> str:
    """A file of Q1 answers with two columns of marks for it."""
    rows = [f"{encrypted_id(student)},{answer},{marks_a},{marks_b}" for student, answer, marks_a, marks_b in students]
    return "\n".join(["StudentID,Q1,Marks A,Marks B", *rows]) + "\n"


Q1_COLUMNS = {"answer_columns": json.dumps(["Q1"]), "marks_columns": json.dumps({"Q1": "Marks A"})}


def encrypted_original() -> SimpleUploadedFile:
    return SimpleUploadedFile("original", ENCRYPTED_FILE, "application/octet-stream")


def test_accepts_the_ids_and_file_the_browser_encrypts(client: Client) -> None:
    assessment = create_assessment(client)
    csv = f"StudentID,Q1\n{GOLDEN['encrypted_id']},yes\n"
    columns = {"answer_columns": json.dumps(["Q1"]), "marks_columns": "{}"}
    assert upload(client, assessment, csv, original=encrypted_original(), **columns).status_code == 200
    assert list(assessment.students.values_list("encrypted_id", flat=True)) == [GOLDEN["encrypted_id"]]


def test_keeps_the_encrypted_original_and_the_import_settings(client: Client) -> None:
    assessment = create_assessment(client)
    assert upload(client, assessment, original=encrypted_original(), **FILE_SETTINGS).status_code == 200

    file = SubmissionsFile.objects.get()
    assert (file.sheet, file.header_row, file.first_row, file.last_row) == ("Results", 2, 3, 5)
    assert file.original is not None
    assert bytes(file.original) == ENCRYPTED_FILE
    page = client.get(reverse("import", args=[assessment.pk]))
    assert b"Check columns" in page.content
    assert b'<script id="import-settings" type="application/json">' in page.content
    assert page.context["import_settings"] == {
        "name": "exam.csv",
        "sheet": "Results",
        "header_row": 2,
        "first_row": 3,
        "last_row": 5,
        "student_id_column": "StudentID",
        "answer_columns": ["Q1", "Q2", "Q3"],
        "marks_columns": {"Q1": "Q1 Pts"},
        "file_pattern": "",
        "file_questions": {},
        "id_mapping": None,
    }
    fetched = client.get(reverse("import_original", args=[assessment.pk]))
    assert fetched.status_code == 200
    assert fetched.content == ENCRYPTED_FILE


def test_keeps_a_zips_pattern_and_file_questions(client: Client) -> None:
    assessment = create_assessment(client)
    file_questions = {"game.py": "game", "board.py": "game", "test.py": ""}
    zip_fields = {"file_pattern": "*_{student:number}_{number}_{file}", "file_questions": json.dumps(file_questions)}
    assert upload(client, assessment, original=encrypted_original(), **zip_fields).status_code == 200

    page = client.get(reverse("import", args=[assessment.pk]))
    settings = page.context["import_settings"]
    assert (settings["file_pattern"], settings["file_questions"]) == (
        "*_{student:number}_{number}_{file}",
        file_questions,
    )
    assert b"Check students and files" in page.content
    assert b"Question document" not in page.content, "ExamSoft's question document is for its exports, not zips"


def test_keeps_the_encrypted_id_mapping_file_and_its_columns(client: Client) -> None:
    assessment = create_assessment(client)
    id_mapping = {"file_name": "ids.csv", "match_column": "plab ID", "id_column": "Email"}
    mapping_file = SimpleUploadedFile("id_mapping_file", ENCRYPTED_FILE, "application/octet-stream")
    response = upload(
        client,
        assessment,
        original=encrypted_original(),
        id_mapping=json.dumps(id_mapping),
        id_mapping_file=mapping_file,
    )
    assert response.status_code == 200

    assert client.get(reverse("import", args=[assessment.pk])).context["import_settings"]["id_mapping"] == id_mapping
    fetched = client.get(reverse("import_id_mapping_file", args=[assessment.pk]))
    assert fetched.content == ENCRYPTED_FILE

    upload(client, assessment, original=encrypted_original())
    assert client.get(reverse("import_id_mapping_file", args=[assessment.pk])).status_code == 404


def test_an_id_mapping_file_that_isnt_encrypted_is_refused(client: Client) -> None:
    assessment = create_assessment(client)
    plaintext = SimpleUploadedFile("id_mapping_file", b"plab ID,Email\nplab1001,a@example.edu\n" * 4)
    id_mapping = json.dumps({"file_name": "ids.csv", "match_column": "plab ID", "id_column": "Email"})
    response = upload(client, assessment, id_mapping=id_mapping, id_mapping_file=plaintext)
    assert response.status_code == 400
    assert b"encrypted" in response.content


def test_an_original_that_isnt_encrypted_is_refused(client: Client) -> None:
    assessment = create_assessment(client)
    plaintext = SimpleUploadedFile("original", b"StudentID,Q1\nS001,yes\n" * 4)
    response = upload(client, assessment, original=plaintext)
    assert response.status_code == 400
    assert b"encrypted" in response.content
    assert not assessment.students.exists()


def test_an_oversized_original_is_refused(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(forms, "MAX_FILE_BYTES", len(ENCRYPTED_FILE) - 1)
    assessment = create_assessment(client)
    response = upload(client, assessment, original=encrypted_original())
    assert response.status_code == 400
    assert b"too large" in response.content


def test_older_imports_ask_for_the_file_again(client: Client, assessment: Assessment) -> None:
    page = client.get(reverse("import", args=[assessment.pk]))
    assert b"Check columns" not in page.content
    assert b"Drop the new submissions file here" in page.content
    assert b"import-settings" not in page.content
    assert client.get(reverse("import_original", args=[assessment.pk])).status_code == 404


def test_a_replace_knows_the_questions_a_new_file_would_drop(client: Client, assessment: Assessment) -> None:
    page = client.get(reverse("import", args=[assessment.pk]))
    assert page.context["questions"] == ["Q1", "Q2", "Q3"]
    assert b"Marking is kept." not in page.content, "said once the file shows which questions stay"


def test_changing_columns_replaces_the_results_and_keeps_adjustments(client: Client, user: User) -> None:
    assessment = create_assessment(client)
    csv = two_marks_columns_csv(("S001", "yes", "1", "2"), ("S002", "no", "0", "1"), ("S003", "yes", "1", "0"))
    upload(client, assessment, csv, original=encrypted_original(), **Q1_COLUMNS, **FILE_SETTINGS)
    s001 = assessment.students.get(encrypted_id=encrypted_id("S001"))
    Adjustment.objects.create(student=s001, question=assessment.questions.get(), marks=0.5)

    changed = upload(
        client,
        assessment,
        two_marks_columns_csv(("S001", "yes", "1", "2"), ("S002", "no", "0", "1")),
        **Q1_COLUMNS | {"marks_columns": json.dumps({"Q1": "Marks B"})},
        original=encrypted_original(),
        **FILE_SETTINGS | {"last_row": "4"},
    )

    assert changed.status_code == 200
    assert sorted(assessment.students.values_list("imported_marks", flat=True), key=str) == [{"Q1": 1.0}, {"Q1": 2.0}]
    marks_by_student = {row.student.encrypted_id: row.awards["Q1"].marks for row in assessment_results(assessment).rows}
    assert marks_by_student[encrypted_id("S002")] == 1, "without marking, a student has the submissions file's marks"
    assert Adjustment.objects.get().student == s001
    current, older = SubmissionsFile.objects.all()
    assert (current.last_row, current.marks_columns) == (4, {"Q1": "Marks B"})
    assert current.original is not None
    assert older.original is None


def test_an_import_returns_the_questions_whose_answers_changed(assessment: Assessment, user: User) -> None:
    def changed_by(csv: str) -> set[str]:
        columns = ["Q1", "Q2", "Q3"]
        file = SubmissionsFile(name="exam.csv", student_id_column="StudentID", answer_columns=columns)
        imported = import_submissions(assessment, user, file, csv)
        return imported.changed_questions

    changed = EXAM_CSV.replace("respiration", "fermentation")
    assert changed_by(EXAM_CSV) == set()
    assert changed_by(changed) == {"Q1"}
    assert changed_by(changed.rsplit("\n", 2)[0] + "\n") == {"Q1", "Q2", "Q3"}, "a student fewer changes every question"


def test_an_import_stays_on_the_import_page_with_what_it_did(client: Client) -> None:
    assessment = create_assessment(client)
    response = upload(client, assessment)
    assert response.headers["HX-Redirect"] == reverse("import", args=[assessment.pk])
    page = client.get(response.headers["HX-Redirect"], follow=True).content
    assert b"Imported 10 students and 3 questions." in page
    assert reverse("question_document", args=[assessment.pk]).encode() in page


def test_a_replace_says_which_students_it_added_and_removed(client: Client, assessment: Assessment) -> None:
    rows = [("S011", "light", "1", "42", "A"), ("S002", "changed", "1", "42.0", "B"), *EXAM_ROWS[2:]]
    csv = "StudentID,Q1,Q1 Pts,Q2,Q3\n" + "".join(f"{encrypted_id(row[0])},{','.join(row[1:])}\n" for row in rows)
    response = upload(client, assessment, csv)
    page = client.get(response.headers["HX-Redirect"], follow=True).text
    assert "Submissions file replaced: 10 students (1 new, 1 removed); 1 with changed answers." in page


EXAM_WITHOUT_Q3 = "".join(line.rsplit(",", 1)[0] + "\n" for line in EXAM_CSV.splitlines())


def test_a_replace_counts_changed_answers_only_in_questions_both_files_have(
    client: Client, assessment: Assessment
) -> None:
    response = upload(client, assessment, EXAM_WITHOUT_Q3, answer_columns=json.dumps(["Q1", "Q2"]))
    page = client.get(response.headers["HX-Redirect"], follow=True).text
    assert "Submissions file replaced: 10 students; removed question Q3." in page


def test_activity_names_the_question_of_a_marking_removed_with_it(client: Client, assessment: Assessment) -> None:
    full_marks_marking(assessment, "Q3")
    upload(client, assessment, EXAM_WITHOUT_Q3, answer_columns=json.dumps(["Q1", "Q2"]))
    page = client.get(reverse("activity", args=[assessment.pk]), headers=HTMX_HEADERS).text
    assert "removed marking for Q3" in page
    assert "question group" not in page


def test_a_first_import_sets_up_the_questions_the_core_infers(client: Client) -> None:
    assessment = create_assessment(client)
    upload(client, assessment)
    config = AnswersCsv(student_id_column="StudentID", answer_columns=["Q1", "Q2", "Q3"])
    inferred = QuestionSet({}).updated_from(config.read(EXAM_CSV))
    assert {question.key: question.core_question for question in assessment.questions.all()} == dict(inferred)


def test_a_failed_import_says_why_in_plain_text(client: Client) -> None:
    assessment = create_assessment(client)
    response = upload(client, assessment, "StudentID,Q1,Marks A,Marks B\nS001,yes,1,2\n", **Q1_COLUMNS)
    assert response.status_code == 400
    assert response.headers["Content-Type"].startswith("text/plain")
    assert response.text == "Student IDs must be encrypted in the browser before they're uploaded."
    assert not assessment.students.exists()


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({"answer_columns": json.dumps("Q1")}, "Choose at least one answer column."),
        ({"answer_columns": json.dumps([])}, "Choose at least one answer column."),
        ({"marks_columns": json.dumps(["Marks A"])}, "Choose which question each marks column is for."),
        ({"name": "x" * 256}, "Ensure this value has at most 255 characters (it has 256)."),
    ],
)
def test_malformed_choices_are_refused_plainly(client: Client, fields: dict[str, str], error: str) -> None:
    assessment = create_assessment(client)
    response = upload(client, assessment, **fields)
    assert (response.status_code, response.text) == (400, error)
    assert not assessment.students.exists()


def test_a_file_that_isnt_text_is_refused(client: Client) -> None:
    assessment = create_assessment(client)
    response = upload(client, assessment, answers_csv=SimpleUploadedFile("answers.csv", b"\xff\xfe\x00binary"))
    assert (response.status_code, response.text) == (400, "The file couldn't be read.")


def test_a_large_class_uploads(client: Client) -> None:
    """Larger than Django's limit for a form field, so it has to arrive as a file."""
    assessment = create_assessment(client)
    answer = "photosynthesis " * 200
    csv = two_marks_columns_csv(*((f"S{index:04}", answer, "1", "2") for index in range(1200)))
    assert len(csv) > 2.5 * 2**20
    response = upload(client, assessment, csv, **Q1_COLUMNS)
    assert response.status_code == 200, response.content
    assert assessment.students.count() == 1200


def q1_answers_csv(*answers: str) -> str:
    """A file of Q1 answers, one student each."""
    return "StudentID,Q1\n" + "".join(
        f"{encrypted_id(f'S{index:03}')},{answer}\n" for index, answer in enumerate(answers)
    )


def upload_with_key(client: Client, assessment: Assessment, csv: str, key: dict[str, str]) -> None:
    fields = {"answer_columns": json.dumps(list(key)), "marks_columns": "{}", "answer_key": json.dumps(key)}
    assert upload(client, assessment, csv, **fields).status_code == 200


def inferred_questions(csv: str, key: dict[str, str]) -> QuestionSet:
    """The questions the core works out from a file's answers and its answer key."""
    submissions = AnswersCsv(student_id_column="StudentID", answer_columns=list(key)).read(csv)
    return QuestionSet({}).updated_from([*submissions, read_answer_key(key)])


def test_the_answer_key_shapes_the_questions_and_is_no_student(client: Client) -> None:
    assessment = create_assessment(client)
    csv, key = q1_answers_csv("A", "B", "A"), {"Q1": "A,C"}
    upload_with_key(client, assessment, csv, key)

    assert assessment.questions.get().core_question == inferred_questions(csv, key)["Q1"]
    assert assessment.students.count() == SubmissionsFile.objects.get().student_count == 3
    assert not Marking.objects.exists()


def test_a_replaced_submissions_file_keeps_the_questions_set_up_accepting_its_answers_and_works_out_new_ones(
    client: Client,
) -> None:
    assessment = create_assessment(client)
    upload_with_key(client, assessment, q1_answers_csv("A", "B", "A"), {"Q1": ""})
    question = assessment.questions.get()
    question.core_question = question.core_question.model_copy(update={"max_marks": 5})
    question.save()

    csv = "StudentID,Q1,Q2\n" + "".join(f"{encrypted_id(f'S{index:03}')},C,{index}\n" for index in range(3))
    key = {"Q1": "D", "Q2": ""}
    upload_with_key(client, assessment, csv, key)

    first, second = assessment.questions.order_by("position")
    assert first.core_question == question.core_question.accepting(["C", "D"])
    assert first.definition["options"] == ["a", "b", "c", "d"]
    assert second.core_question == inferred_questions(csv, key)["Q2"]


@pytest.mark.parametrize("key", [{"Q9": "A"}, {"Q1": 1}, ["A"]], ids=["other column", "not text", "not by column"])
def test_a_malformed_answer_key_is_refused(client: Client, key: object) -> None:
    assessment = create_assessment(client)
    response = upload(
        client, assessment, q1_answers_csv("A"), answer_columns=json.dumps(["Q1"]), answer_key=json.dumps(key)
    )
    assert (response.status_code, response.text) == (
        400,
        "The answer key must give a cell for answer columns only.",
    )


def question_document(*items: tuple[int, str, str]) -> SimpleUploadedFile:
    """An ExamSoft question document: each item's number, stem with choices as RTF, and weight."""
    body = "".join(
        rf"\pard Question #: {number}\par\pard {stem}\par\pard Item ID: 1 / 1\par\pard Item Weight: {weight}\par"
        for number, stem, weight in items
    )
    return SimpleUploadedFile("Midterm.doc", rf"{{\rtf1\ansi {body}}}".encode(), "application/msword")


def read_by_the_core(file: SimpleUploadedFile, assessment: Assessment) -> QuestionSet:
    content = file.read()
    file.seek(0)
    return read_question_document(content, assessment.question_set())


def test_the_import_page_offers_the_question_document_once_there_are_questions(client: Client) -> None:
    assessment = create_assessment(client)
    assert b"Question document" not in client.get(reverse("import", args=[assessment.pk])).content
    upload(client, assessment)
    page = client.get(reverse("import", args=[assessment.pk])).text
    assert 'data-confirm="Replace question settings?"' in page


def test_a_question_document_describes_its_questions(client: Client, assessment: Assessment) -> None:
    file = question_document(
        (1, r"How many?\par\pard\u160?1\u160?\par\pard 1. 42|42.0|", "3.0"),
        (3, r"Which?\par\pard A. One\par\pard B. Two\par\pard E. Five", "2.0"),
        (9, "Unasked", "1.0"),
    )
    described = read_by_the_core(file, assessment)
    with TestCase.captureOnCommitCallbacks(execute=True):
        response = client.post(reverse("question_document", args=[assessment.pk]), {"file": file}, headers=HTMX_HEADERS)

    assert response.headers["HX-Redirect"] == reverse("questions", args=[assessment.pk])
    messages = [message.message for message in get_messages(response.wsgi_request)]
    assert set(described) == {"Q1", "Q3"}
    assert messages[-1] == "Set up 2 questions from the question document."
    assert {key: assessment.questions.get(key=key).core_question for key in described} == dict(described)


def test_a_question_document_returns_the_questions_it_changed(assessment: Assessment) -> None:
    data = question_document((1, "How many?", "3.0"), (3, "Which?", "2.0")).read()
    assert apply_question_document(assessment, data) == (["Q1", "Q3"], {"Q1", "Q3"})
    assert apply_question_document(assessment, data) == (["Q1", "Q3"], set())


def test_a_question_document_without_the_exams_questions_says_so(client: Client, assessment: Assessment) -> None:
    file = question_document((9, "Unasked", "1.0"))
    response = client.post(reverse("question_document", args=[assessment.pk]), {"file": file}, headers=HTMX_HEADERS)
    assert response.status_code == 422
    assert "The question document has none of the submissions file&#x27;s questions." in response.text
    assert not any(question.core_question.description for question in assessment.questions.all())


def test_a_file_the_core_cant_read_says_why(client: Client, assessment: Assessment) -> None:
    file = SimpleUploadedFile("Midterm.pdf", b"%PDF-1.7")
    with pytest.raises(GradeFlowError) as refused:
        read_by_the_core(file, assessment)
    response = client.post(reverse("question_document", args=[assessment.pk]), {"file": file}, headers=HTMX_HEADERS)
    assert response.status_code == 422
    assert escape(str(refused.value)) in response.text
    assert not any(question.core_question.description for question in assessment.questions.all())


def test_a_question_document_is_required(client: Client, assessment: Assessment) -> None:
    response = client.post(reverse("question_document", args=[assessment.pk]), headers=HTMX_HEADERS)
    assert response.status_code == 422
    assert "Choose the question document." in response.text


def test_the_questions_with_marks_in_the_file_are_read_in_one_query(
    assessment: Assessment, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    assessment.students.create(encrypted_id="S100", answers={}, imported_marks={"Q2": 1})
    with django_assert_num_queries(1):
        assert imported_mark_keys(assessment) == {"Q1", "Q2"}
