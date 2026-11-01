import json

from allauth.usersessions.adapter import get_adapter
from allauth.usersessions.models import UserSession
from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from gradeflow_app.accounts.forms import ProfileForm
from gradeflow_app.requests import Request
from gradeflow_app.ui.templatetags.ui import browser


def account_settings(request: Request) -> HttpResponse:
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect("settings")
    themes = [("system", "System"), ("light", "Light"), ("dark", "Dark")]
    return render(request, "accounts/settings.html", {"form": form, "themes": themes})


def integrations(request: Request) -> HttpResponse:
    """What this device connects to: one of the server's AI services, whose models load with what the device keeps,
    and Canvas, when this server can reach it."""
    proxy = settings.GRADEFLOW_CORS_PROXY_URL
    context = {
        "providers": settings.GRADEFLOW_MODEL_PROVIDERS,
        "canvas_config": json.dumps({"proxy": proxy}) if proxy else "",
    }
    return render(request, "accounts/integrations.html", context)


@require_POST
def sign_out_device(request: Request, session: int) -> HttpResponse:
    """Signs one of the person's other devices out; this one signs out from the account menu."""
    device = get_object_or_404(UserSession, pk=session, user=request.user)
    if device.is_current():
        raise Http404
    get_adapter().end_sessions([device])
    messages.success(request, f"Signed out {browser(device.user_agent)}.")
    return redirect("usersessions_list")
