"""Forms of a method: a new one, a saved marking, a copy, and a new setting."""

from functools import partial
from typing import Any

from django import forms

from gradeflow_app.questions.models import QUESTION_TYPE_LABELS

label_field = partial(forms.CharField, label="Name", max_length=100)
summary_field = partial(forms.CharField, label="What it does", max_length=300)


class NewMethodForm(forms.Form):
    label = label_field(widget=forms.TextInput(attrs={"placeholder": "Mentions photosynthesis"}))
    summary = summary_field(
        required=False, widget=forms.TextInput(attrs={"placeholder": "Correct if the answer mentions photosynthesis."})
    )
    question_type = forms.ChoiceField(label="Question type", choices=QUESTION_TYPE_LABELS)


class SaveMethodForm(forms.Form):
    label = label_field()
    summary = summary_field(required=False, help_text="One sentence, shown when people choose a method.")


class CopyForm(forms.Form):
    label = label_field()
    library = forms.ChoiceField(error_messages={"required": "Choose where to copy it."})

    def __init__(self, choices: list[tuple[str, str]], *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        library = self.fields["library"]
        assert isinstance(library, forms.ChoiceField)
        library.choices = choices


class NewSettingForm(forms.Form):
    name = forms.CharField(
        label="Name",
        help_text="How the method's steps refer to it, such as pass_mark.",
        widget=forms.TextInput(attrs={"class": "font-mono", "autocomplete": "off", "spellcheck": "false"}),
    )
