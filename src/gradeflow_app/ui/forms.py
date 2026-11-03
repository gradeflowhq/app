from collections.abc import Mapping
from typing import Any

from django import forms
from django.forms.renderers import TemplatesSetting
from django.forms.widgets import Input


def first_error(form: forms.BaseForm) -> str:
    return str(next(iter(form.errors.values()))[0])


def _error_class(widget: forms.Widget) -> str | None:
    if isinstance(widget, forms.Select):
        return "select-error"
    if isinstance(widget, forms.Textarea):
        return "textarea-error"
    if isinstance(widget, forms.FileInput):
        return "file-input-error"
    if isinstance(widget, Input) and not isinstance(widget, forms.CheckboxInput):
        return "input-error"
    return None


class BoundField(forms.BoundField):
    """Marks a control whose value has errors with daisyUI's error colour. Leaves out `required`, so an empty field
    gets the server's error beside it rather than the browser's bubble."""

    @property
    def is_optional(self) -> bool:
        return not self.field.required and self.field.initial is None

    def build_widget_attrs(self, attrs: dict[str, Any], widget: forms.Widget | None = None) -> dict[str, Any]:
        attrs = super().build_widget_attrs(attrs, widget)
        attrs.pop("required", None)
        widget = widget or self.field.widget
        error = self.errors and _error_class(widget)
        if error:
            attrs["class"] = f"{widget.attrs['class']} {error}" if "class" in widget.attrs else error
        return attrs


class MarkdownInput(forms.Textarea):
    """Markdown people write, with formatting buttons and a preview (markdown.js). Disabled, it shows as pages show
    it."""

    template_name = "forms/markdown.html"

    def __init__(self) -> None:
        super().__init__(attrs={"rows": 3, "class": "field-sizing-content max-h-[60vh]"})

    def get_context(self, name: str, value: Any, attrs: dict[str, Any] | None) -> dict[str, Any]:
        context = super().get_context(name, value, attrs)
        if not context["widget"]["attrs"].get("disabled"):
            context["widget"]["attrs"] |= {"x-ref": "text", "x-show": "writing", "x-on:keydown": "keydown"}
        return context


class Renderer(TemplatesSetting):
    form_template_name = "forms/form.html"
    field_template_name = "forms/field.html"
    bound_field_class = BoundField


def posted_indexes(data: Mapping[str, Any], prefix: str) -> list[int]:
    """The indexes of the rows posted as `<prefix>-<index>…`, in order."""
    found = {
        int(part)
        for key in data
        if key.startswith(f"{prefix}-") and (part := key[len(prefix) + 1 :].split("-", 1)[0]).isdigit()
    }
    return sorted(found)
