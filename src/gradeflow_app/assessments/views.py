from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.forms import AssessmentForm
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.courses.models import Role
from gradeflow_app.imports.views import importer_context
from gradeflow_app.requests import Request


@assessment_member(Role.EDITOR)
def assessment_settings(request: Request, access: AssessmentAccess) -> HttpResponse:
    """The submissions file and question document for editors; the name, description and Delete for owners."""
    if request.method == "POST" and not access.is_owner:
        raise PermissionDenied
    form = AssessmentForm(request.POST or None, instance=access.assessment)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect("assessment_settings", access.assessment.pk)
    return render(request, "assessments/settings.html", {"access": access, "form": form, **importer_context(access)})


@require_POST
@assessment_member(Role.OWNER)
def delete_assessment(request: Request, access: AssessmentAccess) -> HttpResponse:
    name = access.assessment.name
    access.assessment.delete()
    messages.success(request, f"Deleted “{name}”.")
    return redirect("course", access.course.pk)
