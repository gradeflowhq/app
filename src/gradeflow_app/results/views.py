from functools import cache, partial
from typing import Literal, NamedTuple
from uuid import UUID

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from gradeflow_core import InvalidError, ResultsCsv

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.imports.models import Student
from gradeflow_app.questions.models import Question
from gradeflow_app.requests import Request
from gradeflow_app.results.forms import download_form
from gradeflow_app.results.marks import Award, Level, Results, StudentMarks, assessment_results, is_blank
from gradeflow_app.ui.fragments import fragment_of, is_fragment
from gradeflow_app.ui.templatetags.ui import marks

GRID_PAGE_SIZE = 50
"""Students on a page of the Students grid."""

type CellState = Level | Literal["blank", "needs_marking", "not_marked"]


class Cell(NamedTuple):
    """A student's marks for a question in the Students grid."""

    marks: str
    order: float
    """Sorts the column; -1 without marks."""
    state: CellState
    """The marks' level, or why there is none."""
    adjusted: bool


def grid(results: Results) -> list[tuple[StudentMarks, list[Cell]]]:
    """Each student's marks and their cells, in question order."""
    is_blank_by_key = {question.key: cache(partial(is_blank, question)) for question in results.questions}
    format_marks = cache(marks)

    def cell(award: Award, is_blank_answer: bool) -> Cell:
        value = award.marks
        state: CellState
        if not award.is_marked:
            state = "needs_marking"
        elif is_blank_answer and not value:
            state = "blank"
        else:
            state = award.level or "not_marked"
        return Cell(format_marks(value), -1 if value is None else value, state, award.adjustment is not None)

    return [
        (
            row,
            [cell(row.awards[key], is_blank_by_key[key](row.student.answers.get(key, ""))) for key in is_blank_by_key],
        )
        for row in results.rows
    ]


@assessment_member()
def students(request: Request, access: AssessmentAccess) -> HttpResponse:
    template = "results/students.html#students" if is_fragment(request) else "results/students.html"
    results = assessment_results(access.assessment)
    context = {
        "access": access,
        "results": results,
        "grid": grid(results),
        "page_size": GRID_PAGE_SIZE,
        "download_form": download_form(),
        "unmarked_count": results.unmarked_count,
    }
    return render(request, template, context)


@assessment_member()
@fragment_of("students")
def student(request: Request, access: AssessmentAccess, student_id: UUID) -> HttpResponse:
    return student_page(request, access, get_object_or_404(access.assessment.students, pk=student_id))


def student_page(
    request: Request, access: AssessmentAccess, student: Student, adjusted_question: Question | None = None
) -> HttpResponse:
    """The student drawer, scrolled to the question `?question=` names, or with focus on the Adjust button of
    `adjusted_question`."""
    student_results = assessment_results(access.assessment, [student])
    (row,) = student_results.rows
    rows = []
    for question in student_results.questions:
        award = row.awards[question.key]
        method = (
            award.result.method_label if award.result else "Imported marks" if award.imported_marks is not None else ""
        )
        if award.result and award.result.model_names:
            method += f" ({', '.join(award.result.model_names)})"
        answer = student.answers.get(question.key, "")
        rows.append((question, answer, is_blank(question, answer), award, method))
    return render(
        request,
        "results/student.html",
        {
            "access": access,
            "student": student,
            "rows": rows,
            "total": row.total,
            "max_marks": student_results.max_marks,
            "focused_question_id": request.GET.get("question", ""),
            "adjusted_question": adjusted_question,
        },
    )


@require_POST
@assessment_member()
def download(request: Request, access: AssessmentAccess) -> HttpResponse:
    form = download_form(request.POST)
    if form.is_valid():
        try:
            csv_format = ResultsCsv.load(form.values())
        except InvalidError as error:
            form.add_problems(error.problems)
        else:
            name = f"{access.assessment.name} results.csv".replace("/", "-")
            disposition = content_disposition_header(as_attachment=True, filename=name)
            # With a byte order mark, Excel reads the file as UTF-8.
            data = _csv(access, csv_format).encode("utf-8-sig")
            return HttpResponse(
                data, content_type="text/csv; charset=utf-8", headers={"Content-Disposition": disposition}
            )
    context = {
        "access": access,
        "download_form": form,
        "unmarked_count": assessment_results(access.assessment).unmarked_count,
    }
    return render(request, "results/download.html#form", context, status=422)


def _csv(access: AssessmentAccess, csv_format: ResultsCsv) -> str:
    results = assessment_results(access.assessment)
    submissions = [results.marked_submission(row) for row in results.rows]
    return csv_format.write(access.assessment.question_set(), submissions)
