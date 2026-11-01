import codecs
import csv
import io
import math
import re

import pytest
from django.test import Client
from django.urls import reverse

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.live.topics import topic
from gradeflow_app.results.marks import Stats, awards, histogram, results
from gradeflow_app.results.models import Adjustment, Result
from gradeflow_app.ui.templatetags.ui import percent
from tests.conftest import HTMX, create_assessment, envelope, start_marking


def test_stats_describe_the_spread_of_totals() -> None:
    stats = Stats.of([4, 0, 3, 1, 2])
    assert stats is not None
    assert (stats.students, stats.mean, stats.minimum, stats.maximum) == (5, 2, 0, 4)
    assert (stats.q1, stats.median, stats.q3) == (1, 2, 3), "quartiles as spreadsheets count them"
    assert math.isclose(stats.stdev, math.sqrt(2)), "the whole class, not a sample of it"


def test_one_student_is_their_own_quartiles_and_none_have_no_stats() -> None:
    stats = Stats.of([7])
    assert stats is not None
    assert (stats.q1, stats.median, stats.q3, stats.stdev) == (7, 7, 7, 0)
    assert Stats.of([]) is None


def test_a_short_test_has_a_bar_per_mark() -> None:
    assert histogram([0.0, 1.0, 1.0, 2.0], 2) == [(0, 0, 1), (1, 1, 2), (2, 2, 1)]


@pytest.mark.parametrize(("students", "max_marks", "bands"), [(30, 25, 5), (400, 25, 13), (400, 100, 20)])
def test_bands_widen_as_the_class_shrinks(students: int, max_marks: int, bands: int) -> None:
    counts = [band.students for band in histogram([float(i % (max_marks + 1)) for i in range(students)], max_marks)]
    assert len(counts) == bands
    assert sum(counts) == students


def test_bands_may_be_a_round_width_finer_or_coarser() -> None:
    totals = [float(i % 23) for i in range(320)]
    assert {finer: len(histogram(totals, 22, finer)) for finer in (-1, 0, 1)} == {-1: 5, 0: 11, 1: 23}


def test_whole_totals_go_no_finer_than_a_bar_per_mark() -> None:
    assert histogram([0.0, 1.0, 2.0], 2, finer=1) == histogram([0.0, 1.0, 2.0], 2)


def test_totals_with_half_marks_may_fall_in_half_mark_bands() -> None:
    assert [band.students for band in histogram([0.5, 1.5] * 200, 5)] == [0, 200, 0, 200, 0, 0, 0, 0, 0, 0]


def test_bands_end_at_the_maximum_marks() -> None:
    bands = histogram([0.5, 4.5, 6.0] * 20, 6)
    assert (bands[0].start, bands[-1].end) == (0, 6)
    assert bands[-1].students == 20, "full marks fall in the last band"


def test_downloads_are_named_after_the_assessment(client: Client, assessment: Assessment) -> None:
    assessment.name = "Midterm / Résumé"
    assessment.save()
    response = client.post(reverse("download", args=[assessment.pk]), {"student_id_column": "student_id"})
    assert response.headers["Content-Disposition"] == (
        "attachment; filename*=utf-8''Midterm%20-%20R%C3%A9sum%C3%A9%20results.csv"
    )


def test_downloads_open_as_utf8_in_spreadsheets(client: Client, assessment: Assessment) -> None:
    response = client.post(reverse("download", args=[assessment.pk]), {"student_id_column": "student_id"})
    assert response.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert response.content.startswith(codecs.BOM_UTF8 + b"student_id")


def test_there_is_nothing_to_download_before_an_import(client: Client) -> None:
    assessment = create_assessment(client)
    for name in ("students", "publish"):
        page = client.get(reverse(name, args=[assessment.pk])).content.decode()
        assert 'data-dialog="download"' not in page
        assert "data-decrypt" not in page


def test_a_download_needs_a_student_id_column(client: Client, assessment: Assessment) -> None:
    response = client.post(reverse("download", args=[assessment.pk]), {"student_id_column": ""})
    assert response.status_code == 422
    assert "Required." in response.content.decode()
    assert '<details class="group/more" open' in response.content.decode()


@pytest.fixture
def adjusted(assessment: Assessment) -> Assessment:
    """`assessment`, where only Q1's marking has marked, with adjustments to marks, to feedback, and to questions
    without marks."""
    students = {student.key: student for student in assessment.students.all()}
    questions = {question.key: question for question in assessment.questions.all()}
    for student in students.values():
        feedback = "Good." if student.key == envelope("S006") else ""
        Result.objects.create(
            student=student,
            question=questions["Q1"],
            proportion=student.file_marks["Q1"],
            feedback=feedback,
            method="Key Points",
            revision=0,
        )
    adjustments: list[tuple[str, str, float | None, str | None]] = [
        ("S001", "Q1", 0.5, None),
        ("S003", "Q1", None, "Mentions chlorophyll."),
        ("S002", "Q2", 1, "Rounded."),
        ("S004", "Q3", None, "See me."),
    ]
    for who, key, marks, note in adjustments:
        Adjustment.objects.create(student=students[envelope(who)], question=questions[key], marks=marks, feedback=note)
    return assessment


def downloaded(client: Client, assessment: Assessment) -> dict[str, dict[str, str]]:
    """Each student's row of the downloaded CSV, by their ID's envelope."""
    ticked = dict.fromkeys(("include_answers", "include_marks", "include_feedback", "include_total"), "on")
    response = client.post(reverse("download", args=[assessment.pk]), {"student_id_column": "id", **ticked})
    return {row["id"]: row for row in csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))}


def test_adjustments_replace_the_marks_and_feedback_they_set(client: Client, adjusted: Assessment) -> None:
    rows = downloaded(client, adjusted)

    def given(student: str, key: str) -> tuple[str, str]:
        row = rows[envelope(student)]
        return row[f"{key} marks"], row[f"{key} feedback"]

    assert given("S001", "Q1") == ("0.5", "")
    assert (given("S002", "Q1"), given("S002", "Q2")) == (("1", ""), ("1", "Rounded."))
    assert given("S003", "Q1") == ("0", "Mentions chlorophyll.")
    assert (given("S004", "Q1"), given("S004", "Q3")) == (("0", ""), ("", "")), "feedback alone gives no marks"
    assert given("S006", "Q1") == ("1", "Good.")
    assert rows[envelope("S002")]["total marks"] == "2"


def test_totals_count_adjusted_marks_and_every_adjustment(adjusted: Assessment) -> None:
    assessment_results = results(adjusted)
    rows = {row.student.key: (row.total, sorted(row.adjusted)) for row in assessment_results.rows}
    assert rows[envelope("S001")] == (0.5, ["Q1"])
    assert rows[envelope("S002")] == (2, ["Q2"])
    assert rows[envelope("S004")] == (0, ["Q3"])
    summaries = [
        (s.question.key, s.marked, s.average, s.full, s.zero, s.blank, s.adjusted)
        for s in assessment_results.summaries()
    ]
    assert summaries == [("Q1", 10, 0.35, 3, 6, 1, 2), ("Q2", 1, 1, 1, 0, 1, 1), ("Q3", 0, None, 0, 0, 0, 1)]


def test_a_students_page_shows_their_adjustments(client: Client, adjusted: Assessment) -> None:
    student = adjusted.students.get(key=envelope("S001"))
    page = client.get(reverse("student", args=[adjusted.pk, student.pk]), headers=HTMX).content.decode()
    assert "The marking gives 1." in page
    assert 'value="0.5"' in page
    student = adjusted.students.get(key=envelope("S002"))
    page = client.get(reverse("student", args=[adjusted.pk, student.pk]), headers=HTMX).content.decode()
    assert "The marking gives" not in page
    assert page.count("Rounded.") == 2, "as the feedback and in the form"
    assert "· </span>" not in page, "Q3 has no marking to name"


def test_the_students_list_refreshes_as_marks_change(client: Client, assessment: Assessment) -> None:
    url = reverse("students", args=[assessment.pk])
    page = client.get(url).content.decode()
    assert f'hx-get="{url}" hx-trigger="{topic("marks", assessment.pk)} from:body' in page
    assert "average 0.4 / 3" in page
    student = assessment.students.get(key=envelope("S001"))
    question = assessment.questions.get(key="Q2")
    Adjustment.objects.create(student=student, question=question, marks=1)
    refreshed = client.get(url, headers=HTMX).content.decode()
    assert "<html" not in refreshed
    assert "average 0.5 / 3" in refreshed


def test_the_questions_list_averages_the_marks_the_overview_does(client: Client, adjusted: Assessment) -> None:
    listed = client.get(reverse("question_list", args=[adjusted.pk]), headers=HTMX).content.decode()
    averages = re.findall(r'title="Average mark">([^<]+)<', listed)
    overview = [percent(s.average_proportion) for s in results(adjusted).summaries() if s.average is not None]
    assert averages == overview == ["35%", "100%"], "adjustments count, as in the overview"


def test_without_marking_a_student_has_the_exams_marks(client: Client, assessment: Assessment) -> None:
    rows = downloaded(client, assessment)
    assert [rows[envelope(student)]["Q1 marks"] for student in ("S001", "S003")] == ["1", "0"]
    assert rows[envelope("S001")]["Q2 marks"] == "", "nothing for a question without marks"


def test_activity_tells_clearing_adjustments_from_making_them(client: Client, adjusted: Assessment) -> None:
    adjusted.students.get(key=envelope("S001")).adjustments.get().delete()
    page = client.get(reverse("activity", args=[adjusted.pk]), headers=HTMX).content.decode()
    assert "cleared adjustments to Q1 for 1 student" in page
    assert "adjusted Q1 for 2 students" in page


@pytest.mark.parametrize("target", ["student", "answers"])
def test_adjustment_request_can_clear_feedback_without_changing_zero_marks(
    client: Client, assessment: Assessment, target: str
) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.get(key=envelope("S001"))
    Result.objects.create(
        student=student, question=question, proportion=1, feedback="Generated feedback", method="Saved", revision=0
    )
    page_url = (
        reverse("student", args=[assessment.pk, student.pk])
        if target == "student"
        else reverse("answers", args=[assessment.pk, question.pk])
    )
    assert b'name="clear_feedback"' in client.get(page_url, headers=HTMX).content
    url = reverse("adjust", args=[assessment.pk, question.pk])
    fields = {"student": str(student.pk), "marks": "0", "feedback": "", "then": target, "action": "apply"}
    cleared = client.post(url, fields | {"clear_feedback": "on"}, headers=HTMX)
    assert cleared.status_code == 200
    adjustment = Adjustment.objects.get(student=student, question=question)
    assert (adjustment.marks, adjustment.feedback) == (0, "")
    awarded = awards([question], [student])[student.pk, question.pk]
    assert (awarded.marks, awarded.feedback) == (0, "")

    assert client.post(url, fields, headers=HTMX).status_code == 200
    adjustment.refresh_from_db()
    assert (adjustment.marks, adjustment.feedback) == (0, None)
    awarded = awards([question], [student])[student.pk, question.pk]
    assert (awarded.marks, awarded.feedback) == (0, "Generated feedback")


def test_an_answer_the_question_reads_as_blank_is_blank_everywhere(client: Client, assessment: Assessment) -> None:
    student = assessment.students.get(key=envelope("S003"))
    student.answers["Q1"] = "N/A"
    student.save()
    question = assessment.questions.get(key="Q1")

    assert results(assessment).summaries()[0].blank == 2
    url = reverse("answers", args=[assessment.pk, question.pk])
    page = client.get(url, {"filter": "blank"}, headers=HTMX).content.decode()
    assert "2 students" in page


def test_marks_that_round_to_the_maximum_are_full(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    for student in assessment.students.all():
        Result.objects.create(
            student=student, question=question, proportion=0.7 + 0.2 + 0.1, method="Weighted", revision=0
        )

    summary = results(assessment).summaries()[0]
    assert (summary.full, summary.zero) == (10, 0)
    url = reverse("answers", args=[assessment.pk, question.pk])
    assert "1 student" in client.get(url, {"filter": "full"}, headers=HTMX).content.decode()
    assert "1 student" not in client.get(url, {"filter": "part"}, headers=HTMX).content.decode()
    first = assessment.students.first()
    assert first is not None
    assert awards([question], [first])[first.pk, question.pk].level == "full"


def test_the_overview_says_how_each_question_is_marked(client: Client, assessment: Assessment) -> None:
    page = client.get(reverse("overview", args=[assessment.pk])).content.decode()
    assert "From the exam export" in page, "nothing is marked yet"
    assert page.count("Imported marks") == 1
    assert page.count("Not marked") == 2
    assert "Adjusted</th>" not in page, "no column while no one is adjusted"


def test_a_draft_doesnt_mark_a_question_on_the_overview(client: Client, assessment: Assessment) -> None:
    start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    page = client.get(reverse("overview", args=[assessment.pk])).content.decode()
    assert page.count("Not marked") == 2, "only saved marking counts"
    assert "Accepted Answers" not in page


def test_the_adjusted_column_shows_once_someone_is_adjusted(client: Client, adjusted: Assessment) -> None:
    for name in ("overview", "students"):
        assert "Adjusted</th>" in client.get(reverse(name, args=[adjusted.pk])).content.decode(), name


def test_a_run_of_changes_to_a_questions_adjustments_shows_once(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.get(key=envelope("S001"))
    for action in ("apply", "clear", "apply"):
        fields = {"student": str(student.pk), "marks": "1", "action": action}
        client.post(reverse("adjust", args=[assessment.pk, question.pk]), fields, headers=HTMX)
    page = client.get(reverse("activity", args=[assessment.pk]), headers=HTMX).content.decode()
    assert page.count("adjusted marks for Q1") == 1
    assert "cleared adjustments" not in page
    assert f'href="{reverse("question", args=[assessment.pk, question.pk])}"' in page


def test_downloads_are_only_posted_from_the_dialog(client: Client, assessment: Assessment) -> None:
    assert client.get(reverse("download", args=[assessment.pk])).status_code == 405
    for name in ("students", "publish"):
        assert 'data-dialog="download"' in client.get(reverse(name, args=[assessment.pk])).content.decode(), name
