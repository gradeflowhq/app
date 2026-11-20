from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from gradeflow_app.landing.page import landing_context


@login_not_required
def landing(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("courses")
    return render(request, "landing/page.html", landing_context())
