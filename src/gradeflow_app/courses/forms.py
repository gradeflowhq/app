from django import forms

from gradeflow_app.courses.models import Course, Role


class CourseForm(forms.ModelForm[Course]):
    class Meta:
        model = Course
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Introductory Biology"}),
            "description": forms.Textarea(attrs={"rows": 2, "placeholder": "Semester 1, 2026"}),
        }
        labels = {"name": "Name"}


class InviteForm(forms.Form):
    email = forms.EmailField(label="Email address", widget=forms.EmailInput(attrs={"placeholder": "name@example.edu"}))
    role = forms.TypedChoiceField(choices=Role, coerce=Role, initial=Role.EDITOR)


class RoleForm(forms.Form):
    role = forms.TypedChoiceField(choices=Role, coerce=Role)
