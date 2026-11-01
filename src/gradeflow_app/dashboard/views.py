from django.http import HttpResponse
from django.shortcuts import render

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.models import Membership
from gradeflow_app.dashboard import overview as overviews
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import part_of


@member()
def overview(request: Request, membership: Membership) -> HttpResponse:
    return render(request, "assessments/overview.html", overviews.context(membership))


@member()
@part_of("overview")
def activity(request: Request, membership: Membership) -> HttpResponse:
    entries = overviews.activity(membership.assessment, limit=100)
    return render(request, "assessments/activity.html", {"membership": membership, "entries": entries})
