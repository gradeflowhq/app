from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render

from gradeflow_app.landing import page
from gradeflow_app.landing.forms import ExampleChoiceForm


@login_not_required
def landing(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        return redirect("courses")
    return render(request, "landing/page.html", {**page.landing_context(), "choice": ExampleChoiceForm()})
