from django import forms
from django.http import QueryDict
from gradeflow_core import ResultsCsv

from gradeflow_app.ui.schemaforms.forms import ObjectForm


class AdjustmentForm(forms.Form):
    marks = forms.FloatField(required=False, error_messages={"invalid": "Enter marks as a number."})
    clear_feedback = forms.BooleanField(required=False)
    feedback = forms.CharField(required=False, strip=False, empty_value=None)
    """None keeps the marking's feedback."""

    def __init__(self, data: QueryDict, max_marks: float) -> None:
        super().__init__(data)
        self.max_marks = max_marks

    def clean_marks(self) -> float | None:
        marks: float | None = self.cleaned_data["marks"]
        if marks is not None and not 0 <= marks <= self.max_marks:
            raise forms.ValidationError(f"Marks must be between 0 and {self.max_marks:g}.")
        return marks

    def clean_feedback(self) -> str | None:
        feedback: str | None = self.cleaned_data["feedback"]
        return "" if self.cleaned_data["clear_feedback"] else feedback


def download_form(data: QueryDict | None = None) -> ObjectForm:
    """The download dialog's options, which pages with a Download button render."""
    schema = ResultsCsv.model_json_schema()
    return ObjectForm(schema, {}, data=data)
