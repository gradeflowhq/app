import codecs
from uuid import UUID

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from gradeflow_core import InvalidError, ResultsCsv

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.imports.models import Student
from gradeflow_app.requests import Request
from gradeflow_app.results.forms import download_form
from gradeflow_app.results.marks import assessment_results
from gradeflow_app.ui.fragments import fragment_of, is_fragment


@assessment_member()
def students(request: Request, membership: AssessmentAccess) -> HttpResponse:
    template = "results/students.html#students" if is_fragment(request) else "results/students.html"
    results = assessment_results(membership.assessment)
    context = {
        "membership": membership,
        "results": results,
        "has_adjustments": results.has_adjustments,
        "download_form": download_form(),
    }
    return render(request, template, context)


@assessment_member()
@fragment_of("students")
def student(request: Request, membership: AssessmentAccess, student_id: UUID) -> HttpResponse:
    return student_page(request, membership, get_object_or_404(membership.assessment.students, pk=student_id))


def student_page(request: Request, membership: AssessmentAccess, student: Student) -> HttpResponse:
    student_results = assessment_results(membership.assessment, [student])
    (row,) = student_results.rows
    rows = []
    for question in student_results.questions:
        award = row.awards[question.key]
        method = award.result.method if award.result else "Imported marks" if award.imported_marks is not None else ""
        if award.result and award.result.language_models:
            method += f" ({', '.join(award.result.language_models)})"
        rows.append((question, question.core_question.normalize(student.answers.get(question.key, "")), award, method))
    return render(
        request,
        "results/student.html",
        {
            "membership": membership,
            "student": student,
            "rows": rows,
            "total": row.total,
            "max_marks": student_results.max_marks,
        },
    )


@require_POST
@assessment_member()
def download(request: Request, membership: AssessmentAccess) -> HttpResponse:
    form = download_form(request.POST)
    if form.is_valid():
        try:
            csv_format = ResultsCsv.load(form.values())
        except InvalidError as error:
            form.add_problems(error.problems)
        else:
            name = f"{membership.assessment.name} results.csv".replace("/", "-")
            disposition = content_disposition_header(as_attachment=True, filename=name)
            # With a byte order mark, Excel reads the file as UTF-8.
            data = codecs.BOM_UTF8 + _csv(membership, csv_format).encode()
            return HttpResponse(
                data, content_type="text/csv; charset=utf-8", headers={"Content-Disposition": disposition}
            )
    return render(request, "results/download.html#form", {"membership": membership, "download_form": form}, status=422)


def _csv(membership: AssessmentAccess, csv_format: ResultsCsv) -> str:
    results = assessment_results(membership.assessment)
    submissions = [results.marked_submission(row) for row in results.rows]
    return csv_format.write(membership.assessment.question_set(), submissions)
