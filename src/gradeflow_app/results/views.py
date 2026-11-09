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
def students(request: Request, access: AssessmentAccess) -> HttpResponse:
    template = "results/students.html#students" if is_fragment(request) else "results/students.html"
    return render(request, template, {"access": access, "results": assessment_results(access.assessment)})


@assessment_member()
@fragment_of("students")
def student(request: Request, access: AssessmentAccess, student_id: UUID) -> HttpResponse:
    return student_page(request, access, get_object_or_404(access.assessment.students, pk=student_id))


def student_page(request: Request, access: AssessmentAccess, student: Student) -> HttpResponse:
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
        rows.append((question, question.core_question.normalize(student.answers.get(question.key, "")), award, method))
    return render(
        request,
        "results/student.html",
        {
            "access": access,
            "student": student,
            "rows": rows,
            "total": row.total,
            "max_marks": student_results.max_marks,
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
    return render(request, "results/download.html#form", {"access": access, "download_form": form}, status=422)


def _csv(access: AssessmentAccess, csv_format: ResultsCsv) -> str:
    results = assessment_results(access.assessment)
    submissions = [results.marked_submission(row) for row in results.rows]
    return csv_format.write(access.assessment.question_set(), submissions)
