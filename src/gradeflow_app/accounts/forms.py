from django import forms
from django.http import HttpRequest

from gradeflow_app.accounts.models import User


class SignupForm(forms.Form):
    """The fields allauth's sign-up form adds to email and password."""

    name = forms.CharField(max_length=150, label="Your name")

    field_order = ("name",)

    def signup(self, request: HttpRequest, user: User) -> None:
        user.name = self.cleaned_data["name"]
        user.save(update_fields=["name"])


class ProfileForm(forms.ModelForm[User]):
    name = SignupForm.base_fields["name"]

    class Meta:
        model = User
        fields = ("name",)
