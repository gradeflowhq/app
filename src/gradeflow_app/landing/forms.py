"""The landing page's settings: which example shows, and each example's setting."""

from django import forms
from gradeflow_core import BUILTINS

from gradeflow_app.landing.content import content
from gradeflow_app.ui.schemaforms.widgets import Segmented


def submitted(value: object) -> str:
    """A setting's value as the browser submits it: the form's values for its name, joined by commas."""
    if isinstance(value, bool):
        return "on" if value else ""
    if isinstance(value, list):
        return ",".join(value)
    return str(value)


def choices(key: str) -> list[tuple[str, str]]:
    return list(content().examples[key].choices.items())


class Settings(forms.Form):
    """Which example shows, and each example's setting, by the example's name."""

    assumption = forms.ChoiceField(widget=Segmented, initial="0.5", choices=choices("assumption"))
    conditional = forms.BooleanField(initial=True, widget=forms.CheckboxInput(attrs={"class": "toggle toggle-primary"}))
    custom = forms.ChoiceField(widget=Segmented, initial="0.5", choices=choices("custom"))
    tests = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple, initial=list(content().examples["tests"].choices), choices=choices("tests")
    )

    def __init__(self) -> None:
        super().__init__()
        landing_examples = content().examples
        tabs = [(key, BUILTINS.by_ref[example.method].label) for key, example in landing_examples.items()]
        example = forms.ChoiceField(widget=Segmented, initial=tabs[0][0], choices=tabs)
        self.fields = {"example": example, **self.fields}

    def initial_values(self) -> dict[str, str]:
        return {name: submitted(self[name].initial) for name in self.fields}
