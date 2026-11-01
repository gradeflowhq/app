from django import forms

from gradeflow_app.assessments.models import Assessment


class AssessmentForm(forms.ModelForm[Assessment]):
    class Meta:
        model = Assessment
        fields = ("name", "description")
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "Midterm"}),
            "description": forms.Textarea(attrs={"rows": 2, "placeholder": "12 March 2026"}),
        }
        labels = {"name": "Name"}
