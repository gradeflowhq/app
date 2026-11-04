"""The form field for each kind of property, as its type and the core's hints say."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django import forms

from gradeflow_app.ui.forms import MarkdownInput
from gradeflow_app.ui.schemaforms.schema import (
    SUGGESTIONS_KEY,
    Schema,
    hint,
    labels_of,
    resolve,
    title_of,
    unwrap_nullable,
)
from gradeflow_app.ui.schemaforms.widgets import (
    CodeInput,
    ConditionWidget,
    DecimalInput,
    FeedbackWidget,
    OrderedChoices,
    Segmented,
    TagsWidget,
)

MAX_SEGMENTS = 3
"""The most choices shown side by side, as segments."""
MAX_SEGMENT_LABEL_CHARS = 24
"""The most characters of their labels together, for segments: longer ones don't fit the marking column in one row
at 1,024 px."""
MAX_CHECKBOXES = 8


def as_number(text: str) -> int | float | str:
    """The number `text` is, or `text` itself if it isn't one, for the core to say what's wrong with it."""
    try:
        return int(text) if not any(mark in text.lower() for mark in ".e") else float(text)
    except ValueError:
        return text


class NumberField(forms.Field):
    """A number; one that doesn't read as a number is kept as typed, as is one out of range."""

    def __init__(self, *, is_integer: bool, **kwargs: Any) -> None:
        self.is_integer = is_integer
        widget = forms.NumberInput() if is_integer else DecimalInput(attrs={"step": "any"})
        super().__init__(widget=widget, **kwargs)

    def to_python(self, value: Any) -> int | float | str | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return int(text) if self.is_integer else as_number(text)
        except ValueError:
            return text


class ListField(forms.Field):
    """A list of strings or numbers, edited as tags."""

    def __init__(self, *, is_numeric: bool, suggestions: Mapping[str, int], **kwargs: Any) -> None:
        self.is_numeric = is_numeric
        super().__init__(widget=TagsWidget(suggestions), **kwargs)

    def to_python(self, value: Any) -> list[Any]:
        items = [text for item in value or [] if (text := str(item).strip())]
        return [as_number(item) for item in items] if self.is_numeric else items


def field_for(schema: Schema, label: str, help_text: str, defs: Mapping[str, Schema]) -> forms.Field | None:
    """A form field for a property; `field_hints` has what else the core says of it. None are required: the core
    says what is. Text shows the core's first example as its placeholder."""
    made = _field_for(schema, label, help_text, defs)
    if made is not None:
        made.initial = schema.get("default")
        examples = schema.get("examples") or ()
        if examples and isinstance(examples[0], str):
            made.widget.attrs["placeholder"] = examples[0]
    return made


def _field_for(schema: Schema, label: str, help_text: str, defs: Mapping[str, Schema]) -> forms.Field | None:
    property_schema, nullable = unwrap_nullable(schema)
    kwargs: dict[str, Any] = {"label": label, "help_text": help_text, "required": False}
    kind = property_schema.get("type")
    hints = hint(property_schema)
    if "enum" in property_schema:
        choices = _choices(property_schema, property_schema["enum"])
        kwargs["coerce"] = _type_of(property_schema["enum"])
        if len(choices) <= MAX_SEGMENTS and sum(len(label) for _, label in choices) <= MAX_SEGMENT_LABEL_CHARS:
            return forms.TypedChoiceField(choices=choices, widget=Segmented, **kwargs)
        # A list shows its first choice as chosen: without a default among them, it starts with none.
        blank = [] if schema.get("default") in property_schema["enum"] else [("", f"Choose {label.lower()}")]
        return forms.TypedChoiceField(choices=[*blank, *choices], widget=forms.Select, **kwargs)
    if kind == "boolean":
        return forms.BooleanField(**kwargs)
    if hints.get("kind") == "feedback":
        return forms.Field(widget=FeedbackWidget(_boxes(schema, defs), hints.get("template_parts") or {}), **kwargs)
    if kind == "string":
        made = _text_field(hints, kwargs)
        if nullable and isinstance(made, forms.CharField):
            made.empty_value = None  # left empty, it is unset
        return made
    if kind in ("integer", "number"):
        return NumberField(is_integer=kind == "integer", **kwargs)
    if kind == "array":
        return _list_field(property_schema, kwargs)
    return None


def _choices(property_schema: Schema, options: Iterable[Any]) -> list[tuple[Any, str]]:
    """Each of `options` with its label, or else itself."""
    labels = labels_of(property_schema)
    return [(value, labels.get(value) or str(value)) for value in options]


def _boxes(schema: Schema, defs: Mapping[str, Schema]) -> list[tuple[str, str]]:
    """The outcomes feedback may be written for, by name and title: the fields of its object form."""
    members = (resolve(option, defs) for option in schema.get("anyOf", ()))
    outcomes = next((member for member in members if member.get("type") == "object"), {})
    return [(name, title_of(property_schema, name)) for name, property_schema in outcomes.get("properties", {}).items()]


def _text_field(hints: Mapping[str, Any], kwargs: dict[str, Any]) -> forms.Field:
    """A text field as the core's hint says it is typed: a condition, code or Markdown."""
    match hints.get("kind"):
        case "condition":
            return forms.Field(widget=ConditionWidget, **kwargs)
        case "code":
            return forms.CharField(widget=CodeInput(hints.get("language", "")), strip=False, **kwargs)
        case "markdown":
            return forms.CharField(widget=MarkdownInput(), strip=False, **kwargs)
    return forms.CharField(strip=False, **kwargs)


def _type_of(options: Sequence[Any]) -> type:
    """The type a choice among `options` is posted back as: a number's options are numbers."""
    kinds = {type(option) for option in options}
    return kinds.pop() if len(kinds) == 1 and kinds <= {int, float} else str


def _list_field(property_schema: Schema, kwargs: dict[str, Any]) -> forms.Field:
    """A list of choices (a set as checkboxes or tags, or in order with repeats), or of values typed in."""
    items, _ = unwrap_nullable(property_schema.get("items", {}))
    if "enum" in items:
        choices = _choices(property_schema, items["enum"])
        if not property_schema.get("uniqueItems"):
            widget: forms.Widget = OrderedChoices(str(hint(property_schema).get("item_label") or "item").capitalize())
        elif len(choices) <= MAX_CHECKBOXES:
            widget = forms.CheckboxSelectMultiple()
        else:
            widget = TagsWidget(create=False)
        return forms.MultipleChoiceField(choices=choices, widget=widget, **kwargs)
    kinds = items.get("type")
    is_numeric = set(kinds if isinstance(kinds, list) else [kinds]) <= {"number", "integer"}
    suggestions = property_schema.get(SUGGESTIONS_KEY) or {}
    return ListField(is_numeric=is_numeric, suggestions=suggestions, **kwargs)
