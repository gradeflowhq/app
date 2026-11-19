from django.http import HttpResponse
from django.shortcuts import render

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.dashboard import overview as overviews
from gradeflow_app.imports.views import importer_context
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import fragment_of


@assessment_member()
def overview(request: Request, access: AssessmentAccess) -> HttpResponse:
    context = overviews.overview_context(access)
    if context["progress"].latest_import is None and access.can_edit:
        context |= importer_context(access)
    return render(request, "assessments/overview.html", context)


@assessment_member()
@fragment_of("overview")
def activity(request: Request, access: AssessmentAccess) -> HttpResponse:
    events = overviews.activity(access.assessment, limit=100)
    return render(request, "assessments/activity.html", {"access": access, "events": events})
