"""The landing page's choice of which method example shows."""

from django import forms

from gradeflow_app.landing.content import core_output
from gradeflow_app.ui.schemaforms.widgets import Segmented


def example_labels() -> dict[str, str]:
    """Each example's method label, by the example's key."""
    return {key: example.method_label for key, example in core_output().examples.items()}


class ExampleChoiceForm(forms.Form):
    example = forms.ChoiceField(widget=Segmented, initial=lambda: next(iter(example_labels())), choices=example_labels)
