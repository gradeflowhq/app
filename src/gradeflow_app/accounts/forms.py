from django import forms

from gradeflow_app.accounts.allauth_forms import SignupForm
from gradeflow_app.accounts.models import User


class ProfileForm(forms.ModelForm[User]):
    name = SignupForm.base_fields["name"]

    class Meta:
        model = User
        fields = ("name",)
