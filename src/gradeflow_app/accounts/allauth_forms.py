"""allauth's forms with plain labels. The browser sends authentication keys instead of passwords (password-forms.js)."""

from typing import Any, ClassVar

from allauth.account import forms as allauth
from django import forms
from django.http import HttpRequest

from gradeflow_app.accounts.models import User
from gradeflow_app.accounts.signals import key_pair_replaced

MIN_PASSWORD_LENGTH = 8
"""Checked in the browser, since the server never sees the password."""


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
            field.help_text = ""
        if password1 := self.fields.get("password1"):
            password1.help_text = f"At least {MIN_PASSWORD_LENGTH} characters."
            password1.widget.attrs["minlength"] = MIN_PASSWORD_LENGTH


def _hidden_user_field(name: str) -> forms.Field:
    field: forms.Field = forms.fields_for_model(User, fields=[name], widgets={name: forms.HiddenInput})[name]
    return field


class RewrappedPrivateKeyForm(forms.Form):
    """The new password's KDF salt and the private key wrapped with its wrapping key."""

    kdf_salt = _hidden_user_field("kdf_salt")
    wrapped_private_key = _hidden_user_field("wrapped_private_key")

    def save_keys_to(self, user: User) -> None:
        user.kdf_salt = self.cleaned_data["kdf_salt"]
        user.wrapped_private_key = self.cleaned_data["wrapped_private_key"]


class NewKeyPairForm(RewrappedPrivateKeyForm):
    public_key = _hidden_user_field("public_key")

    def save_keys_to(self, user: User) -> None:
        super().save_keys_to(user)
        user.public_key = self.cleaned_data["public_key"]


class LoginForm(PlainLabelsMixin, allauth.LoginForm):
    pass


class SignupForm(PlainLabelsMixin, NewKeyPairForm, allauth.SignupForm):
    LABELS: ClassVar[dict[str, str]] = {"password1": "Password"}

    name = forms.CharField(max_length=150, label="Your name")

    field_order = ("name",)

    def signup(self, request: HttpRequest, user: User) -> None:
        user.name = self.cleaned_data["name"]
        self.save_keys_to(user)
        user.save(update_fields=["name", "kdf_salt", "public_key", "wrapped_private_key"])


class AddEmailForm(PlainLabelsMixin, allauth.AddEmailForm):
    pass


class ChangePasswordForm(PlainLabelsMixin, RewrappedPrivateKeyForm, allauth.ChangePasswordForm):
    def save(self) -> None:
        self.save_keys_to(self.user)
        super().save()


class ResetPasswordForm(PlainLabelsMixin, allauth.ResetPasswordForm):
    pass


class ResetPasswordKeyForm(PlainLabelsMixin, NewKeyPairForm, allauth.ResetPasswordKeyForm):
    """A reset with the recovery key keeps the key pair. Without it, the account gets a new key pair, and its secrets
    and course keys are deleted, as the new key pair can't decrypt them."""

    def save(self) -> None:
        new_key_pair = self.cleaned_data["public_key"] != self.user.public_key
        self.save_keys_to(self.user)
        if new_key_pair:
            self.user.recovery_wrapped_private_key = ""
        super().save()
        if new_key_pair:
            self.user.secrets.all().delete()
            key_pair_replaced.send(sender=User, user=self.user)


class ConfirmEmailVerificationCodeForm(PlainLabelsMixin, allauth.ConfirmEmailVerificationCodeForm):
    pass


class ConfirmPasswordResetCodeForm(PlainLabelsMixin, allauth.ConfirmPasswordResetCodeForm):
    pass


class ReauthenticateForm(PlainLabelsMixin, allauth.ReauthenticateForm):
    pass
