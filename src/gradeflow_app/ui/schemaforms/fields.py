"""The form field for each kind of property, as its type and the core's hints say."""

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from django import forms

from gradeflow_app.ui.forms import MarkdownInput
from gradeflow_app.ui.schemaforms.schema import (
    SUGGESTIONS,
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

SEGMENTED_UP_TO = 3
"""The most choices shown side by side, as segments."""
SEGMENTED_LABELS_UP_TO = 24
"""The most characters of their labels together, for segments: longer ones don't fit the marking column in one row
at 1,024 px."""
CHECKBOXES_UP_TO = 8


def as_number(text: str) -> int | float | str:
    """The number `text` is, or `text` itself if it isn't one, for the core to say what's wrong with it."""
    try:
        return int(text) if not any(mark in text.lower() for mark in ".e") else float(text)
    except ValueError:
        return text


class NumberField(forms.Field):
    """A number; one that doesn't read as a number is kept as typed, as is one out of range."""

    def __init__(self, *, integer: bool, **kwargs: Any) -> None:
        self.integer = integer
        widget = forms.NumberInput() if integer else DecimalInput(attrs={"step": "any"})
        super().__init__(widget=widget, **kwargs)

    def to_python(self, value: Any) -> int | float | str | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return int(text) if self.integer else as_number(text)
        except ValueError:
            return text


class ListField(forms.Field):
    """A list of strings or numbers, edited as chips."""

    def __init__(self, *, numeric: bool, suggestions: Mapping[str, int], **kwargs: Any) -> None:
        self.numeric = numeric
        super().__init__(widget=TagsWidget(suggestions), **kwargs)

    def to_python(self, value: Any) -> list[Any]:
        items = [text for item in value or [] if (text := str(item).strip())]
        return [as_number(item) for item in items] if self.numeric else items


class ConditionField(forms.Field):
    widget = ConditionWidget


class FeedbackField(forms.Field):
    def __init__(self, *, boxes: Sequence[tuple[str, str]], inserts: Mapping[str, str], **kwargs: Any) -> None:
        super().__init__(widget=FeedbackWidget(boxes, inserts), **kwargs)


def field_for(schema: Schema, label: str, help_text: str, defs: Mapping[str, Schema]) -> forms.Field | None:
    """A form field for a property; `extras_of` has what else the core says of it. None are required: the core
    says what is. Text shows the core's first example as its placeholder."""
    made = _field_for(schema, label, help_text, defs)
    if made is not None:
        made.initial = schema.get("default")
        examples = schema.get("examples") or ()
        if examples and isinstance(examples[0], str):
            made.widget.attrs["placeholder"] = examples[0]
    return made


def _field_for(schema: Schema, label: str, help_text: str, defs: Mapping[str, Schema]) -> forms.Field | None:
    prop, nullable = unwrap_nullable(schema)
    kwargs: dict[str, Any] = {"label": label, "help_text": help_text, "required": False}
    kind = prop.get("type")
    gradeflow = hint(prop)
    if "enum" in prop:
        choices = _choices(prop, prop["enum"])
        kwargs["coerce"] = _type_of(prop["enum"])
        if len(choices) <= SEGMENTED_UP_TO and sum(len(label) for _, label in choices) <= SEGMENTED_LABELS_UP_TO:
            return forms.TypedChoiceField(choices=choices, widget=Segmented, **kwargs)
        # A list shows its first choice as chosen: without a default among them, it starts with none.
        blank = [] if schema.get("default") in prop["enum"] else [("", f"Choose {label.lower()}")]
        return forms.TypedChoiceField(choices=[*blank, *choices], widget=forms.Select, **kwargs)
    if kind == "boolean":
        return forms.BooleanField(**kwargs)
    if gradeflow.get("kind") == "feedback":
        return FeedbackField(boxes=_boxes(schema, defs), inserts=gradeflow.get("inserts") or {}, **kwargs)
    if kind == "string":
        made = _text_field(gradeflow, kwargs)
        if nullable and isinstance(made, forms.CharField):
            made.empty_value = None  # left empty, it is unset
        return made
    if kind in ("integer", "number"):
        return NumberField(integer=kind == "integer", **kwargs)
    if kind == "array":
        return _list_field(prop, kwargs)
    return None


def _choices(prop: Schema, options: Iterable[Any]) -> list[tuple[Any, str]]:
    """Each of `options` with its label, or else itself."""
    labels = labels_of(prop)
    return [(value, labels.get(value) or str(value)) for value in options]


def _boxes(schema: Schema, defs: Mapping[str, Schema]) -> list[tuple[str, str]]:
    """The outcomes feedback may be written for, by name and title: the fields of its object form."""
    members = (resolve(option, defs) for option in schema.get("anyOf", ()))
    outcomes = next((member for member in members if member.get("type") == "object"), {})
    return [(name, title_of(prop, name)) for name, prop in outcomes.get("properties", {}).items()]


def _text_field(gradeflow: Mapping[str, Any], kwargs: dict[str, Any]) -> forms.Field:
    """A text field as the core's hint says it is typed: a condition, code or Markdown."""
    match gradeflow.get("kind"):
        case "condition":
            return ConditionField(**kwargs)
        case "code":
            return forms.CharField(widget=CodeInput(gradeflow.get("language", "")), strip=False, **kwargs)
        case "markdown":
            return forms.CharField(widget=MarkdownInput(), strip=False, **kwargs)
    return forms.CharField(strip=False, **kwargs)


def _type_of(options: Sequence[Any]) -> type:
    """The type a choice among `options` is posted back as: a number's options are numbers."""
    kinds = {type(option) for option in options}
    return kinds.pop() if len(kinds) == 1 and kinds <= {int, float} else str


def _list_field(prop: Schema, kwargs: dict[str, Any]) -> forms.Field:
    """A list of choices (a set as checkboxes or chips, or in order with repeats), or of values typed in."""
    items, _ = unwrap_nullable(prop.get("items", {}))
    if "enum" in items:
        choices = _choices(prop, items["enum"])
        if not prop.get("uniqueItems"):
            widget: forms.Widget = OrderedChoices(str(hint(prop).get("item") or "item").capitalize())
        elif len(choices) <= CHECKBOXES_UP_TO:
            widget = forms.CheckboxSelectMultiple()
        else:
            widget = TagsWidget(create=False)
        return forms.MultipleChoiceField(choices=choices, widget=widget, **kwargs)
    kinds = items.get("type")
    numeric = set(kinds if isinstance(kinds, list) else [kinds]) <= {"number", "integer"}
    suggestions = prop.get(SUGGESTIONS) or {}
    return ListField(numeric=numeric, suggestions=suggestions, **kwargs)
