import codecs
from uuid import UUID

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.utils.http import content_disposition_header
from django.views.decorators.http import require_POST
from gradeflow_core import InvalidError, ResultsCsv

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.models import Membership
from gradeflow_app.imports.models import Student
from gradeflow_app.requests import Request
from gradeflow_app.results.forms import download_form
from gradeflow_app.results.marks import results
from gradeflow_app.ui.fragments import is_fragment, part_of


@member()
def students(request: Request, membership: Membership) -> HttpResponse:
    template = "results/students.html#students" if is_fragment(request) else "results/students.html"
    assessment_results = results(membership.assessment)
    context = {
        "membership": membership,
        "results": assessment_results,
        "adjusted": assessment_results.any_adjusted,
        "download": download_form(),
    }
    return render(request, template, context)


@member()
@part_of("students")
def student(request: Request, membership: Membership, student: UUID) -> HttpResponse:
    return student_page(request, membership, get_object_or_404(membership.assessment.students, pk=student))


def student_page(request: Request, membership: Membership, student: Student) -> HttpResponse:
    sheet = results(membership.assessment, [student])
    (row,) = sheet.rows
    rows = []
    for question in sheet.questions:
        award = row.awards[question.key]
        method = award.result.method if award.result else "Imported marks" if award.file_marks is not None else ""
        if award.result and award.result.language_models:
            method += f" ({', '.join(award.result.language_models)})"
        rows.append((question, question.core.normalize(student.answers.get(question.key, "")), award, method))
    return render(
        request,
        "results/student.html",
        {"membership": membership, "student": student, "rows": rows, "total": row.total, "max_marks": sheet.max_marks},
    )


@require_POST
@member()
def download(request: Request, membership: Membership) -> HttpResponse:
    form = download_form(request.POST)
    if form.is_valid():
        try:
            options = ResultsCsv.load(form.values())
        except InvalidError as error:
            form.add_problems(error.problems)
        else:
            name = f"{membership.assessment.name} results.csv".replace("/", "-")
            disposition = content_disposition_header(as_attachment=True, filename=name)
            # With a byte order mark, Excel reads the file as UTF-8.
            data = codecs.BOM_UTF8 + _csv(membership, options).encode()
            return HttpResponse(
                data, content_type="text/csv; charset=utf-8", headers={"Content-Disposition": disposition}
            )
    return render(request, "results/download.html#form", {"membership": membership, "download": form}, status=422)


def _csv(membership: Membership, options: ResultsCsv) -> str:
    assessment_results = results(membership.assessment)
    marked = [assessment_results.marked(row) for row in assessment_results.rows]
    return options.write(membership.assessment.question_set(), marked)
