"""Forms of a method: a new one, a saved marking, and a copy."""

from functools import partial
from typing import Any

from django import forms

from gradeflow_app.questions.models import TYPE_LABELS

label_field = partial(forms.CharField, label="Name", max_length=100)
summary_field = partial(forms.CharField, label="What it does", max_length=300)


class NewMethodForm(forms.Form):
    label = label_field(widget=forms.TextInput(attrs={"placeholder": "Mentions photosynthesis"}))
    summary = summary_field(
        required=False, widget=forms.TextInput(attrs={"placeholder": "Full marks for mentioning photosynthesis."})
    )
    question_type = forms.ChoiceField(label="Answer type", choices=TYPE_LABELS.items())


class SaveMethodForm(forms.Form):
    label = label_field()
    summary = summary_field(required=False, help_text="One sentence, shown when people choose a method.")


class CopyForm(forms.Form):
    label = label_field()
    library = forms.ChoiceField(error_messages={"required": "Choose where to copy it."})

    def __init__(self, choices: list[tuple[str, str]], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["library"].choices = choices  # type: ignore[attr-defined]
