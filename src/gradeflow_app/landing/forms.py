"""The landing page's settings: which example shows, and each example's setting."""

from django import forms
from gradeflow_core import BUILTINS

from gradeflow_app.landing.content import landing_content
from gradeflow_app.ui.schemaforms.widgets import Segmented


def as_submitted(value: object) -> str:
    """A setting's value as the browser submits it: the form's values for its name, joined by commas."""
    if isinstance(value, bool):
        return "on" if value else ""
    if isinstance(value, list):
        return ",".join(value)
    return str(value)


_EXAMPLES = landing_content().examples


class ExampleSettingsForm(forms.Form):
    """Which example shows, and each example's setting, by the example's name."""

    example = forms.ChoiceField(
        widget=Segmented,
        initial=next(iter(_EXAMPLES)),
        choices={key: BUILTINS.find(example.method).label for key, example in _EXAMPLES.items()},
    )
    workflow = forms.ChoiceField(widget=Segmented, initial="0.5", choices=_EXAMPLES["workflow"].choices)
    conditional = forms.BooleanField(initial=True, widget=forms.CheckboxInput(attrs={"class": "toggle toggle-primary"}))
    custom_code = forms.ChoiceField(widget=Segmented, initial="0.5", choices=_EXAMPLES["custom_code"].choices)
    code_tests = forms.MultipleChoiceField(
        widget=forms.CheckboxSelectMultiple,
        initial=list(_EXAMPLES["code_tests"].choices),
        choices=_EXAMPLES["code_tests"].choices,
    )

    def initial_values(self) -> dict[str, str]:
        return {name: as_submitted(self[name].initial) for name in self.fields}
