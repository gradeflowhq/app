"""JSON schemas as the core writes them, and what its hints say of a field."""

from collections.abc import Mapping
from typing import Any

from django.forms.utils import pretty_name
from gradeflow_core.editing import HINT_KEY

type Schema = dict[str, Any]

SUGGESTIONS_KEY = "x-suggestions"
"""Where a field's schema holds the answers it suggests, with how many gave each: the app's, not the core's."""


def title_of(schema: Schema, name: object) -> str:
    """A field's title, or else its name, like `max_marks`, as words."""
    return schema.get("title") or pretty_name(str(name))


def resolve(schema: Schema, defs: Mapping[str, Schema]) -> Schema:
    while "$ref" in schema:
        schema = {
            **defs[schema["$ref"].rsplit("/", 1)[-1]],
            **{keyword: value for keyword, value in schema.items() if keyword != "$ref"},
        }
    return schema


def unwrap_nullable(schema: Schema) -> tuple[Schema, bool]:
    kind = schema.get("type")
    if isinstance(kind, list) and "null" in kind:
        non_null_types = [type_name for type_name in kind if type_name != "null"]
        return {**schema, "type": non_null_types[0] if len(non_null_types) == 1 else non_null_types}, True
    options = schema.get("anyOf")
    if options and any(option.get("type") == "null" for option in options):
        inner = next(option for option in options if option.get("type") != "null")
        return {**{keyword: value for keyword, value in schema.items() if keyword != "anyOf"}, **inner}, True
    return schema, False


def hint(schema: Schema) -> dict[str, Any]:
    value: dict[str, Any] = schema.get(HINT_KEY, {})
    return value


def labels_of(schema: Schema) -> dict[str, str]:
    """The labels of a choice's options, or of a list of choices'."""
    items, _ = unwrap_nullable(schema.get("items", {}))
    return hint(schema).get("labels") or hint(items).get("labels") or {}


def holds_steps(schema: Schema) -> bool:
    """Whether the field holds a step, or a list of them."""
    return "step" in (hint(schema).get("kind"), hint(schema.get("items", {})).get("kind"))


def is_editable(schema: Schema) -> bool:
    return not schema.get("readOnly") and "const" not in schema
