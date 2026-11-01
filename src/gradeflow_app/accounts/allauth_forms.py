"""allauth's forms in plain words, apart from forms.py, which allauth itself imports for sign-up."""

from typing import Any, ClassVar

from allauth.account import forms as allauth
from django import forms
from django.contrib.auth.password_validation import MinimumLengthValidator, get_default_password_validators


def _password_help() -> str:
    length = next(
        validator for validator in get_default_password_validators() if isinstance(validator, MinimumLengthValidator)
    )
    return f"At least {length.min_length} characters."


class PlainLabels:
    """Sentence-case labels, no placeholders, and help only for a new password. allauth's other help is a
    forgotten-password link, which the pages show by the form."""

    LABELS: ClassVar[dict[str, str]] = {
        "oldpassword": "Current password",
        "password1": "New password",
        "password2": "Confirm new password",
    }
    fields: dict[str, forms.Field]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.label = self.LABELS.get(name, field.label)
            field.widget.attrs.pop("placeholder", None)
            field.help_text = _password_help() if name == "password1" else ""


class LoginForm(PlainLabels, allauth.LoginForm):
    pass


class SignupForm(PlainLabels, allauth.SignupForm):
    LABELS: ClassVar[dict[str, str]] = {"password1": "Password"}


class AddEmailForm(PlainLabels, allauth.AddEmailForm):
    pass


class ChangePasswordForm(PlainLabels, allauth.ChangePasswordForm):
    pass


class SetPasswordForm(PlainLabels, allauth.SetPasswordForm):
    pass


class ResetPasswordForm(PlainLabels, allauth.ResetPasswordForm):
    pass


class ResetPasswordKeyForm(PlainLabels, allauth.ResetPasswordKeyForm):
    pass


class RequestLoginCodeForm(PlainLabels, allauth.RequestLoginCodeForm):
    pass


class ConfirmLoginCodeForm(PlainLabels, allauth.ConfirmLoginCodeForm):
    pass


class ConfirmEmailVerificationCodeForm(PlainLabels, allauth.ConfirmEmailVerificationCodeForm):
    pass


class ConfirmPasswordResetCodeForm(PlainLabels, allauth.ConfirmPasswordResetCodeForm):
    pass


class ReauthenticateForm(PlainLabels, allauth.ReauthenticateForm):
    pass
