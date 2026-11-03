from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.forms.models import model_to_dict
from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.template.defaultfilters import pluralize
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django_htmx.http import HttpResponseClientRedirect

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.courses.models import Role
from gradeflow_app.imports.forms import QuestionDocumentForm, UploadForm
from gradeflow_app.imports.services import ImportFailed, apply_question_document, import_results
from gradeflow_app.marking.remarks import remark
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.requests import Request
from gradeflow_app.ui.forms import first_error

IMPORT_SETTINGS = (
    "name",
    "sheet",
    "header_row",
    "first_row",
    "last_row",
    "student_id_column",
    "answer_columns",
    "marks_columns",
)
"""What the importer needs to reopen a results file as it was imported."""


@assessment_member(Role.EDITOR)
def import_page(request: Request, membership: AssessmentAccess) -> HttpResponse:
    latest_file = membership.assessment.results_files.annotate(has_original=Q(original__isnull=False)).first()
    return render(
        request,
        "imports/import.html",
        {
            "membership": membership,
            "latest_file": latest_file,
            "import_settings": (
                model_to_dict(latest_file, fields=IMPORT_SETTINGS) if latest_file and latest_file.has_original else None
            ),
            "questions": list(membership.assessment.questions.values_list("key", flat=True)),
        },
    )


@require_GET
@assessment_member(Role.EDITOR)
def original(request: Request, membership: AssessmentAccess) -> HttpResponse:
    """The latest results file, as the browser encrypted it."""
    data = membership.assessment.results_files.values_list("original", flat=True).first()
    if data is None:
        raise Http404
    return HttpResponse(bytes(data), content_type="application/octet-stream")


@require_POST
@assessment_member(Role.EDITOR)
def upload(request: Request, membership: AssessmentAccess) -> HttpResponse:
    """Imports the results the importer uploads; a failure is plain text for it to show."""
    assessment = membership.assessment
    form = UploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return _import_error(first_error(form))
    import_settings = form.cleaned_data
    try:
        with transaction.atomic():
            summary = import_results(
                assessment,
                request.user,
                csv_text=import_settings.pop("answers_csv"),
                key_row=import_settings.pop("answer_key"),
                **import_settings,
            )
            remark(assessment, summary.changed_questions, request.user, endpoint(request))
    except ImportFailed as error:
        return _import_error(str(error))
    if summary.first_import:
        messages.success(request, f"Imported {summary.student_count} students and {summary.question_count} questions.")
    else:
        student_changes = [
            f"{count} {change}"
            for count, change in ((summary.new_students, "new"), (summary.removed_students, "removed"))
            if count
        ]
        details = [
            f"{summary.student_count} students" + (f" ({', '.join(student_changes)})" if student_changes else "")
        ]
        if summary.changed_students:
            details.append(f"{summary.changed_students} with changed answers")
        if summary.added_questions:
            details.append(f"new question{pluralize(summary.added_questions)} {', '.join(summary.added_questions)}")
        if summary.removed_questions:
            details.append(
                f"removed question{pluralize(summary.removed_questions)} {', '.join(summary.removed_questions)}"
            )
        messages.success(request, "Results replaced: " + "; ".join(details) + ".")
    return HttpResponseClientRedirect(reverse("import", args=[assessment.pk]))


def _import_error(reason: str) -> HttpResponse:
    return HttpResponse(reason, status=400, content_type="text/plain; charset=utf-8")


@require_POST
@assessment_member(Role.EDITOR)
def question_document(request: Request, membership: AssessmentAccess) -> HttpResponse:
    form = QuestionDocumentForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            with transaction.atomic():
                document_questions, changed = apply_question_document(membership.assessment, form.cleaned_data["file"])
                remark(membership.assessment, changed, request.user, endpoint(request))
        except ImportFailed as failed:
            form.add_error("file", str(failed))
        else:
            count = len(document_questions)
            messages.success(request, f"Set up {count} question{pluralize(count)} from the question document.")
            return HttpResponseClientRedirect(reverse("questions", args=[membership.assessment.pk]))
    context = {"assessment": membership.assessment, "error": form.errors["file"][0]}
    return render(request, "imports/question_document.html", context, status=422)
