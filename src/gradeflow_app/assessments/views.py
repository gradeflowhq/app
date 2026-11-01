from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.forms import AssessmentForm
from gradeflow_app.assessments.models import Membership
from gradeflow_app.courses.models import Role
from gradeflow_app.requests import Request


@member(Role.OWNER)
def assessment_settings(request: Request, membership: Membership) -> HttpResponse:
    form = AssessmentForm(request.POST or None, instance=membership.assessment)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect("assessment_settings", membership.assessment.pk)
    sample = membership.assessment.students.values_list("key", flat=True).first()
    return render(request, "assessments/settings.html", {"membership": membership, "form": form, "sample": sample})


@require_POST
@member(Role.OWNER)
def delete(request: Request, membership: Membership) -> HttpResponse:
    name = membership.assessment.name
    membership.assessment.delete()
    messages.success(request, f"Deleted “{name}”.")
    return redirect("course", membership.course.pk)
