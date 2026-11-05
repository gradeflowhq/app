import base64
import json

from allauth.usersessions.adapter import get_adapter
from allauth.usersessions.models import UserSession
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.crypto import salted_hmac
from django.views.decorators.http import require_http_methods, require_POST

from gradeflow_app.accounts.forms import ProfileForm, RecoveryKeyForm, SecretForm
from gradeflow_app.accounts.models import Secret, User
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
    """The AI service and Canvas connections. Canvas shows only when the server has a CORS proxy."""
    proxy = settings.GRADEFLOW_CORS_PROXY_URL
    context = {
        "providers": settings.GRADEFLOW_MODEL_PROVIDERS,
        "canvas_config": json.dumps({"proxy": proxy}) if proxy else "",
    }
    return render(request, "accounts/integrations.html", context)


@require_POST
def sign_out_device(request: Request, session_id: int) -> HttpResponse:
    """Signs one of the person's other devices out; this one signs out from the account menu."""
    user_session = get_object_or_404(UserSession, pk=session_id, user=request.user)
    if user_session.is_current():
        raise Http404
    get_adapter().end_sessions([user_session])
    messages.success(request, f"Signed out {browser(user_session.user_agent)}.")
    return redirect("usersessions_list")


@login_not_required
@require_POST
def kdf_salt(request: HttpRequest) -> JsonResponse:
    """The KDF salt for an email. An unknown email gets a dummy salt, so the response doesn't reveal who has an
    account."""
    email = request.POST.get("email", "")
    user = User.objects.filter(emailaddress__email__iexact=email).first()
    dummy_salt = salted_hmac("gradeflow.kdf-salt", email.lower(), algorithm="sha256").digest()[:16]
    return JsonResponse({"kdfSalt": user.kdf_salt if user else base64.b64encode(dummy_salt).decode()})


@require_POST
def save_recovery_key(request: Request) -> HttpResponse:
    form = RecoveryKeyForm(request.POST, instance=request.user)
    if not form.is_valid():
        return HttpResponse(status=400)
    form.save()
    return HttpResponse(status=204)


@require_http_methods(["POST", "DELETE"])
def secret(request: Request, name: str) -> HttpResponse:

    if name not in Secret.Name.values:
        raise Http404
    if request.method == "DELETE":
        request.user.secrets.filter(name=name).delete()
        return HttpResponse(status=204)
    form = SecretForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    Secret.objects.update_or_create(
        user=request.user, name=name, defaults={"encrypted_value": form.cleaned_data["encrypted_value"]}
    )
    return HttpResponse(status=204)
