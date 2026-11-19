from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest
from django.urls import reverse


class AccountAdapter(DefaultAccountAdapter):  # type: ignore[misc]
    """allauth's form errors in plain words, and the Security page after a password change."""

    error_messages = DefaultAccountAdapter.error_messages | {
        "duplicate_email": "You've already added this address.",
        "email_password_mismatch": "Wrong email or password.",
        "email_taken": "An account already uses this address.",
        "enter_current_password": "Enter your current password.",
        "incorrect_code": "Wrong code.",
        "incorrect_password": "Wrong password.",
        "rate_limited": "Too many tries. Wait a minute and try again.",
    }

    def get_password_change_redirect_url(self, request: HttpRequest) -> str:
        return reverse("mfa_index")
