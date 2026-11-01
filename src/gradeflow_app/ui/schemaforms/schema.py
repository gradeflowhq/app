"""JSON schemas as the core writes them, and what its hints say of a field."""

from collections.abc import Mapping
from typing import Any

from gradeflow_core.editing import HINT_KEY

type Schema = dict[str, Any]

SUGGESTIONS = "x-suggestions"
"""Where a field's schema holds the answers it suggests, with how many gave each: the app's, not the core's."""


def title_of(schema: Schema, name: object) -> str:
    """A field's title, or else its name, like `max_marks`, as words."""
    return schema.get("title") or str(name).replace("_", " ").strip().capitalize()


def resolve(schema: Schema, defs: Mapping[str, Schema]) -> Schema:
    while "$ref" in schema:
        schema = {**defs[schema["$ref"].rsplit("/", 1)[-1]], **{k: v for k, v in schema.items() if k != "$ref"}}
    return schema


def unwrap_nullable(schema: Schema) -> tuple[Schema, bool]:
    kind = schema.get("type")
    if isinstance(kind, list) and "null" in kind:
        rest = [t for t in kind if t != "null"]
        return {**schema, "type": rest[0] if len(rest) == 1 else rest}, True
    options = schema.get("anyOf")
    if options and any(option.get("type") == "null" for option in options):
        inner = next(option for option in options if option.get("type") != "null")
        return {**{k: v for k, v in schema.items() if k != "anyOf"}, **inner}, True
    return schema, False


def hint(schema: Schema) -> dict[str, Any]:
    value: dict[str, Any] = schema.get(HINT_KEY, {})
    return value


def labels_of(schema: Schema) -> dict[str, str]:
    """The labels of a choice's options, or of a list of choices'."""
    items, _ = unwrap_nullable(schema.get("items", {}))
    return hint(schema).get("labels") or hint(items).get("labels") or {}


def holds_checks(schema: Schema) -> bool:
    """Whether the field holds a check, or a list of them."""
    return "check" in (hint(schema).get("kind"), hint(schema.get("items", {})).get("kind"))


def editable(schema: Schema) -> bool:
    return not schema.get("readOnly") and "const" not in schema
