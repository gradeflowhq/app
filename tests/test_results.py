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
from gradeflow_app.results.marks import Award, TotalStatistics, assessment_results, awards, histogram
from gradeflow_app.results.models import Adjustment, Result
from gradeflow_app.ui.templatetags.ui import percent
from tests.conftest import HTMX_HEADERS, create_assessment, encrypted_id, start_marking


@pytest.mark.parametrize(("imported_marks", "marks", "level"), [(5, 3, "full"), (-1, 0, "no_marks")])
def test_marks_count_from_0_to_the_questions_maximum(imported_marks: float, marks: float, level: str) -> None:
    award = Award(max_marks=3, imported_marks=imported_marks)
    result = award.question_result()
    assert result is not None
    assert (award.marks, award.level, result.marks) == (marks, level, marks)


def test_stats_describe_the_spread_of_totals() -> None:
    statistics = TotalStatistics.of([4, 0, 3, 1, 2])
    assert statistics is not None
    assert (statistics.student_count, statistics.mean, statistics.minimum, statistics.maximum) == (5, 2, 0, 4)
    assert (statistics.lower_quartile, statistics.median, statistics.upper_quartile) == (1, 2, 3), (
        "quartiles as spreadsheets count them"
    )
    assert math.isclose(statistics.standard_deviation, math.sqrt(2)), "the whole class, not a sample of it"


def test_one_student_is_their_own_quartiles_and_none_have_no_stats() -> None:
    statistics = TotalStatistics.of([7])
    assert statistics is not None
    assert (statistics.lower_quartile, statistics.median, statistics.upper_quartile, statistics.standard_deviation) == (
        7,
        7,
        7,
        0,
    )
    assert TotalStatistics.of([]) is None


def test_a_low_maximum_has_a_bar_per_mark() -> None:
    assert histogram([0.0, 1.0, 1.0, 2.0], 2) == [(0, 0, 1), (1, 1, 2), (2, 2, 1)]


@pytest.mark.parametrize(("students", "max_marks", "bands"), [(30, 25, 5), (400, 25, 13), (400, 100, 20)])
def test_bands_widen_as_the_class_shrinks(students: int, max_marks: int, bands: int) -> None:
    counts = [band.students for band in histogram([float(i % (max_marks + 1)) for i in range(students)], max_marks)]
    assert len(counts) == bands
    assert sum(counts) == students


def test_bands_may_be_a_round_width_finer_or_coarser() -> None:
    totals = [float(i % 23) for i in range(320)]
    assert {steps: len(histogram(totals, 22, steps)) for steps in (-1, 0, 1)} == {-1: 5, 0: 11, 1: 23}


def test_whole_totals_go_no_finer_than_a_bar_per_mark() -> None:
    assert histogram([0.0, 1.0, 2.0], 2, narrower_steps=1) == histogram([0.0, 1.0, 2.0], 2)


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
    page = client.get(reverse("export", args=[assessment.pk])).text
    assert 'data-dialog="download"' not in page
    assert "data-decrypt" not in page


def test_a_result_keeps_a_long_method_label(assessment: Assessment) -> None:
    label = "Accepted answers " * 10
    student, question = assessment.students.earliest("pk"), assessment.questions.earliest("pk")
    Result.objects.create(student=student, question=question, proportion=1, method_label=label, revision=0)
    assert Result.objects.get().method_label == label


def test_a_download_needs_a_student_id_column(client: Client, assessment: Assessment) -> None:
    response = client.post(reverse("download", args=[assessment.pk]), {"student_id_column": ""})
    assert response.status_code == 422
    assert "Required." in response.text
    assert '<details class="group/more" open' in response.text


@pytest.fixture
def adjusted_assessment(assessment: Assessment) -> Assessment:
    """`assessment`, where only Q1's marking has marked, with adjustments to marks, to feedback, and to questions
    without marks."""
    students = {student.encrypted_id: student for student in assessment.students.all()}
    questions = {question.key: question for question in assessment.questions.all()}
    for student in students.values():
        Result.objects.create(
            student=student,
            question=questions["Q1"],
            proportion=student.imported_marks["Q1"],
            feedback="Good." if student.encrypted_id == encrypted_id("S006") else "",
            method_label="Key Points",
            revision=0,
        )
    adjustments: list[tuple[str, str, float | None, str | None]] = [
        ("S001", "Q1", 0.5, None),
        ("S003", "Q1", None, "Mentions chlorophyll."),
        ("S002", "Q2", 1, "Rounded."),
        ("S004", "Q3", None, "See me."),
    ]
    for student_id, key, marks, feedback in adjustments:
        Adjustment.objects.create(
            student=students[encrypted_id(student_id)], question=questions[key], marks=marks, feedback=feedback
        )
    return assessment


def downloaded_rows(client: Client, assessment: Assessment) -> dict[str, dict[str, str]]:
    """Each student's row of the downloaded CSV, by their encrypted ID."""
    all_columns = dict.fromkeys(("include_answers", "include_marks", "include_feedback", "include_totals"), "on")
    response = client.post(reverse("download", args=[assessment.pk]), {"student_id_column": "id", **all_columns})
    return {row["id"]: row for row in csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))}


def test_adjustments_replace_the_marks_and_feedback_they_set(client: Client, adjusted_assessment: Assessment) -> None:
    rows = downloaded_rows(client, adjusted_assessment)

    def marks_and_feedback(student: str, key: str) -> tuple[str, str]:
        row = rows[encrypted_id(student)]
        return row[f"{key} marks"], row[f"{key} feedback"]

    assert marks_and_feedback("S001", "Q1") == ("0.5", "")
    assert (marks_and_feedback("S002", "Q1"), marks_and_feedback("S002", "Q2")) == (("1", ""), ("1", "Rounded."))
    assert marks_and_feedback("S003", "Q1") == ("0", "Mentions chlorophyll.")
    assert (marks_and_feedback("S004", "Q1"), marks_and_feedback("S004", "Q3")) == (("0", ""), ("", "")), (
        "feedback alone gives no marks"
    )
    assert marks_and_feedback("S006", "Q1") == ("1", "Good.")
    assert rows[encrypted_id("S002")]["total marks"] == "2"


def test_totals_count_adjusted_marks_and_every_adjustment(adjusted_assessment: Assessment) -> None:
    results = assessment_results(adjusted_assessment)
    rows = {row.student.encrypted_id: (row.total, sorted(row.adjusted_keys)) for row in results.rows}
    assert rows[encrypted_id("S001")] == (0.5, ["Q1"])
    assert rows[encrypted_id("S002")] == (2, ["Q2"])
    assert rows[encrypted_id("S004")] == (0, ["Q3"])
    summaries = [
        (
            summary.question.key,
            summary.marked_count,
            summary.average,
            summary.full_marks_count,
            summary.no_marks_count,
            summary.blank_count,
            summary.adjusted_count,
        )
        for summary in results.summaries()
    ]
    assert summaries == [("Q1", 10, 0.35, 3, 6, 1, 2), ("Q2", 1, 1, 1, 0, 1, 1), ("Q3", 0, None, 0, 0, 0, 1)]


def test_a_students_page_shows_their_adjustments(client: Client, adjusted_assessment: Assessment) -> None:
    student = adjusted_assessment.students.get(encrypted_id=encrypted_id("S001"))
    page = client.get(reverse("student", args=[adjusted_assessment.pk, student.pk]), headers=HTMX_HEADERS).text
    assert "The marking gives 1." in page
    assert 'value="0.5"' in page
    student = adjusted_assessment.students.get(encrypted_id=encrypted_id("S002"))
    page = client.get(reverse("student", args=[adjusted_assessment.pk, student.pk]), headers=HTMX_HEADERS).text
    assert "The marking gives" not in page
    assert page.count("Rounded.") == 2, "as the feedback and in the form"
    assert "· </span>" not in page, "Q3 has no marking to name"


def test_the_students_list_refreshes_as_marks_change(client: Client, assessment: Assessment) -> None:
    url = reverse("students", args=[assessment.pk])
    page = client.get(url).text
    assert f'hx-get="{url}" hx-trigger="{topic("marks", assessment.pk)} from:body' in page
    assert "average 0.4 / 3" in page
    student = assessment.students.get(encrypted_id=encrypted_id("S001"))
    question = assessment.questions.get(key="Q2")
    Adjustment.objects.create(student=student, question=question, marks=1)
    refreshed = client.get(url, headers=HTMX_HEADERS).text
    assert "<html" not in refreshed
    assert "average 0.5 / 3" in refreshed


def test_the_questions_list_averages_the_marks_the_overview_does(
    client: Client, adjusted_assessment: Assessment
) -> None:
    page = client.get(reverse("question_list", args=[adjusted_assessment.pk]), headers=HTMX_HEADERS).text
    averages = re.findall(r'title="Average mark">([^<]+)<', page)
    overview_averages = [
        percent(summary.average_proportion)
        for summary in assessment_results(adjusted_assessment).summaries()
        if summary.average is not None
    ]
    assert averages == overview_averages == ["35%", "100%"], "adjustments count, as in the overview"


def test_without_marking_a_student_has_the_exams_marks(client: Client, assessment: Assessment) -> None:
    rows = downloaded_rows(client, assessment)
    assert [rows[encrypted_id(student)]["Q1 marks"] for student in ("S001", "S003")] == ["1", "0"]
    assert rows[encrypted_id("S001")]["Q2 marks"] == "", "nothing for a question without marks"


def test_activity_tells_clearing_adjustments_from_making_them(client: Client, adjusted_assessment: Assessment) -> None:
    adjusted_assessment.students.get(encrypted_id=encrypted_id("S001")).adjustments.get().delete()
    page = client.get(reverse("activity", args=[adjusted_assessment.pk]), headers=HTMX_HEADERS).text
    assert "cleared adjustments to Q1 for 1 student" in page
    assert "adjusted Q1 for 2 students" in page


@pytest.mark.parametrize("return_to", ["student", "answers"])
def test_adjustment_request_can_clear_feedback_without_changing_no_marks(
    client: Client, assessment: Assessment, return_to: str
) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.get(encrypted_id=encrypted_id("S001"))
    Result.objects.create(
        student=student,
        question=question,
        proportion=1,
        feedback="Generated feedback",
        method_label="Saved",
        revision=0,
    )
    page_url = (
        reverse("student", args=[assessment.pk, student.pk])
        if return_to == "student"
        else reverse("answers", args=[assessment.pk, question.pk])
    )
    assert b'name="clear_feedback"' in client.get(page_url, headers=HTMX_HEADERS).content
    url = reverse("adjust", args=[assessment.pk, question.pk])
    fields = {"student": str(student.pk), "marks": "0", "feedback": "", "return_to": return_to, "action": "apply"}
    cleared = client.post(url, fields | {"clear_feedback": "on"}, headers=HTMX_HEADERS)
    assert cleared.status_code == 200
    adjustment = Adjustment.objects.get(student=student, question=question)
    assert (adjustment.marks, adjustment.feedback) == (0, "")
    award = awards([question], [student])[student.pk, question.pk]
    assert (award.marks, award.feedback) == (0, "")

    assert client.post(url, fields, headers=HTMX_HEADERS).status_code == 200
    adjustment.refresh_from_db()
    assert (adjustment.marks, adjustment.feedback) == (0, None)
    awarded = awards([question], [student])[student.pk, question.pk]
    assert (awarded.marks, awarded.feedback) == (0, "Generated feedback")


def test_an_answer_the_question_reads_as_blank_is_blank_everywhere(client: Client, assessment: Assessment) -> None:
    student = assessment.students.get(encrypted_id=encrypted_id("S003"))
    student.answers["Q1"] = "N/A"
    student.save()
    question = assessment.questions.get(key="Q1")

    assert assessment_results(assessment).summaries()[0].blank_count == 2
    url = reverse("answers", args=[assessment.pk, question.pk])
    page = client.get(url, {"filter": "blank"}, headers=HTMX_HEADERS).text
    assert "2 students" in page


def test_marks_that_round_to_the_maximum_are_full(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    for student in assessment.students.all():
        Result.objects.create(
            student=student, question=question, proportion=0.7 + 0.2 + 0.1, method_label="Weighted", revision=0
        )

    summary = assessment_results(assessment).summaries()[0]
    assert (summary.full_marks_count, summary.no_marks_count) == (10, 0)
    url = reverse("answers", args=[assessment.pk, question.pk])
    assert "1 student" in client.get(url, {"filter": "full"}, headers=HTMX_HEADERS).text
    assert "1 student" not in client.get(url, {"filter": "partial"}, headers=HTMX_HEADERS).text
    first = assessment.students.first()
    assert first is not None
    assert awards([question], [first])[first.pk, question.pk].level == "full"


def test_the_overview_says_how_each_question_is_marked(client: Client, assessment: Assessment) -> None:
    page = client.get(reverse("overview", args=[assessment.pk])).text
    assert "From the submissions file" in page, "nothing is marked yet"
    assert page.count("Imported marks") == 1
    assert page.count("Not marked") == 2
    assert "Adjusted</th>" not in page, "no column while no one is adjusted"


def test_a_draft_doesnt_mark_a_question_on_the_overview(client: Client, assessment: Assessment) -> None:
    start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    page = client.get(reverse("overview", args=[assessment.pk])).text
    assert page.count("Not marked") == 2, "only saved marking counts"
    assert "Accepted Answers" not in page


def test_the_adjusted_column_shows_once_someone_is_adjusted(client: Client, adjusted_assessment: Assessment) -> None:
    for name in ("overview", "students"):
        assert "Adjusted</th>" in client.get(reverse(name, args=[adjusted_assessment.pk])).text, name


def test_a_run_of_changes_to_a_questions_adjustments_shows_once(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    student = assessment.students.get(encrypted_id=encrypted_id("S001"))
    for action in ("apply", "clear", "apply"):
        fields = {"student": str(student.pk), "marks": "1", "action": action}
        client.post(reverse("adjust", args=[assessment.pk, question.pk]), fields, headers=HTMX_HEADERS)
    page = client.get(reverse("activity", args=[assessment.pk]), headers=HTMX_HEADERS).text
    assert page.count("adjusted marks for Q1") == 1
    assert "cleared adjustments" not in page
    assert f'href="{reverse("question", args=[assessment.pk, question.pk])}"' in page


def test_downloads_are_only_posted_from_the_dialog(client: Client, assessment: Assessment) -> None:
    assert client.get(reverse("download", args=[assessment.pk])).status_code == 405
    assert 'data-dialog="download"' in client.get(reverse("export", args=[assessment.pk])).text
