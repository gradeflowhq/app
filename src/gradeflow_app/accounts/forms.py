from django import forms

from gradeflow_app.accounts.allauth_forms import SignupForm
from gradeflow_app.accounts.models import Secret, User


class ProfileForm(forms.ModelForm[User]):
    name = SignupForm.base_fields["name"]

    class Meta:
        model = User
        fields = ("name",)


class RecoveryKeyForm(forms.ModelForm[User]):
    recovery_wrapped_private_key = forms.CharField(max_length=104)

    class Meta:
        model = User
        fields = ("recovery_wrapped_private_key",)


class SecretForm(forms.ModelForm[Secret]):
    class Meta:
        model = Secret
        fields = ("encrypted_value",)
