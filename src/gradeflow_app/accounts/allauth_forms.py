"""allauth's forms in plain words, apart from forms.py, which allauth itself imports for sign-up."""

from typing import Any, ClassVar

from allauth.account import forms as allauth
from django import forms
from django.contrib.auth.password_validation import MinimumLengthValidator, get_default_password_validators


def _password_help() -> str:
    min_length_validator = next(
        validator for validator in get_default_password_validators() if isinstance(validator, MinimumLengthValidator)
    )
    return f"At least {min_length_validator.min_length} characters."


class PlainLabelsMixin:
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


class LoginForm(PlainLabelsMixin, allauth.LoginForm):
    pass


class SignupForm(PlainLabelsMixin, allauth.SignupForm):
    LABELS: ClassVar[dict[str, str]] = {"password1": "Password"}


class AddEmailForm(PlainLabelsMixin, allauth.AddEmailForm):
    pass


class ChangePasswordForm(PlainLabelsMixin, allauth.ChangePasswordForm):
    pass


class SetPasswordForm(PlainLabelsMixin, allauth.SetPasswordForm):
    pass


class ResetPasswordForm(PlainLabelsMixin, allauth.ResetPasswordForm):
    pass


class ResetPasswordKeyForm(PlainLabelsMixin, allauth.ResetPasswordKeyForm):
    pass


class RequestLoginCodeForm(PlainLabelsMixin, allauth.RequestLoginCodeForm):
    pass


class ConfirmLoginCodeForm(PlainLabelsMixin, allauth.ConfirmLoginCodeForm):
    pass


class ConfirmEmailVerificationCodeForm(PlainLabelsMixin, allauth.ConfirmEmailVerificationCodeForm):
    pass


class ConfirmPasswordResetCodeForm(PlainLabelsMixin, allauth.ConfirmPasswordResetCodeForm):
    pass


class ReauthenticateForm(PlainLabelsMixin, allauth.ReauthenticateForm):
    pass
