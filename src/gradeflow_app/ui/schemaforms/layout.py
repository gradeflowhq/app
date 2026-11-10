"""How an object's schema is edited: its fields, groups, rows, maps of tagged values and settings that hold steps."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from itertools import count
from typing import Any

from django import forms

from gradeflow_app.ui.schemaforms.fields import field_for
from gradeflow_app.ui.schemaforms.schema import (
    Schema,
    hint,
    holds_steps,
    is_editable,
    resolve,
    title_of,
    unwrap_nullable,
)

NESTED_SEPARATOR = "__"
"""Joins a nested object's name and its field's in form field names, which become HTML IDs."""

SCALAR_TYPES = frozenset({"string", "number", "integer", "boolean"})


@dataclass(frozen=True)
class Tag:
    """A member of a union of tagged values: `{field: value, "value": ...}`."""

    field: str
    value: str
    label: str
    schema: Schema

    @property
    def is_editable(self) -> bool:
        """Whether its value is one typed in; others, like lists, are kept as they are."""
        return self.schema.get("type") in SCALAR_TYPES


@dataclass(frozen=True)
class TaggedMap:
    """A map of named, tagged values, like Custom Code's parameters."""

    tags: list[Tag]
    item: str
    """What one entry is called."""
    pattern: str = ""
    """What names must look like, as a regular expression."""

    @property
    def editable_tags(self) -> list[Tag]:
        return [tag for tag in self.tags if tag.is_editable]

    def tag_of(self, entry: object) -> Tag | None:
        return next(
            (tag for tag in self.tags if isinstance(entry, Mapping) and entry.get(tag.field) == tag.value), None
        )

    def new_entry(self, taken: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """A name not yet `taken`, and an entry of the first type, its value left to fill in."""
        stem = "_".join(self.item.split())
        name = next(name for number in count(len(taken) + 1) if (name := f"{stem}_{number}") not in taken)
        first = self.editable_tags[0]
        return name, {first.field: first.value}


@dataclass(frozen=True)
class FieldHints:
    """What the core's hints say of a field beyond its type."""

    required: bool = False
    visible_when: Mapping[str, Any] = field(default_factory=dict)
    """Show it only while these fields of the same form have these values."""
    presets: tuple[float, ...] = ()
    """The core's examples of a number, to pick at a click beside the field."""


def field_hints(schema: Schema, required: bool) -> FieldHints:
    property_schema, nullable = unwrap_nullable(schema)
    hints = hint(property_schema)
    numeric = property_schema.get("type") in ("number", "integer")
    return FieldHints(
        required=required and not nullable,
        visible_when=hints.get("applies_when") or {},
        presets=tuple(value for value in schema.get("examples") or () if isinstance(value, int | float))
        if numeric
        else (),
    )


@dataclass
class Layout:
    """How an object's schema is edited."""

    fields: dict[str, forms.Field] = field(default_factory=dict)
    hints: dict[str, FieldHints] = field(default_factory=dict)
    groups: list[tuple[str, list[str]]] = field(default_factory=list)
    """Labelled fieldsets of nested objects' fields."""
    rows: dict[str, Schema] = field(default_factory=dict)
    """Lists of plain objects by name: their item schema."""
    tagged: dict[str, TaggedMap] = field(default_factory=dict)
    """Maps of tagged values by name."""
    headings: dict[str, tuple[str, str]] = field(default_factory=dict)
    """The label and help of each list of rows and map, by name."""
    settings_holding_steps: list[str] = field(default_factory=list)
    """The names of the fields that hold steps."""
    advanced: set[str] = field(default_factory=set)
    """Fields, rows, maps and groups most people leave as they are, by name."""

    def add_group(self, name: str, label: str, nested: Layout) -> None:
        """A nested object's fields, as `<name>__<field>` fields in a labelled group."""
        names = []
        for inner, made in nested.fields.items():
            hints = nested.hints[inner]
            prefixed = f"{name}{NESTED_SEPARATOR}{inner}"
            self.fields[prefixed] = made
            self.hints[prefixed] = replace(
                hints,
                visible_when={f"{name}{NESTED_SEPARATOR}{key}": value for key, value in hints.visible_when.items()},
            )
            if inner in nested.advanced or name in self.advanced:
                self.advanced.add(prefixed)
            names.append(prefixed)
        self.groups.append((label, names))


def _depends_on_basic_choice(
    property_schema: Schema, properties: Mapping[str, Schema], defs: Mapping[str, Schema]
) -> bool:
    """Whether a field applies only once another field leaves its default. If so, it shows next to that field instead
    of under More options, unless that field is there too."""
    wanted = hint(property_schema).get("applies_when") or {}
    choices = [(resolve(properties.get(name, {}), defs), values) for name, values in wanted.items()]
    return bool(wanted) and all(
        not hint(choice).get("advanced") and choice.get("default") not in values for choice, values in choices
    )


def layout(schema: Schema, defs: Mapping[str, Schema] | None = None) -> Layout:
    """The fields of `schema`, whose references are to `defs`, by default its own."""
    defs = schema.get("$defs", {}) if defs is None else defs
    result = Layout()
    required = set(schema.get("required", ()))
    for name, raw in schema.get("properties", {}).items():
        property_schema = resolve(raw, defs)
        if not is_editable(property_schema):
            continue
        label, help_text = title_of(property_schema, name), property_schema.get("description", "")
        if hint(property_schema).get("advanced") and not _depends_on_basic_choice(
            property_schema, schema["properties"], defs
        ):
            result.advanced.add(name)
        if holds_steps(property_schema):
            result.settings_holding_steps.append(name)
        elif property_schema.get("type") == "object" and "properties" in property_schema:
            result.add_group(name, label, layout(property_schema, defs))
        elif (
            property_schema.get("type") == "array"
            and resolve(property_schema.get("items", {}), defs).get("type") == "object"
        ):
            result.rows[name] = resolve(property_schema["items"], defs)
            result.headings[name] = label, help_text
        elif (
            isinstance(property_schema.get("additionalProperties"), dict)
            and "oneOf" in property_schema["additionalProperties"]
        ):
            tags = _tags(property_schema["additionalProperties"], defs)
            item = hint(property_schema).get("item_label") or "entry"
            result.tagged[name] = TaggedMap(tags, item, property_schema.get("propertyNames", {}).get("pattern", ""))
            result.headings[name] = label, help_text
        elif (made := field_for(property_schema, label, help_text, defs)) is not None:
            result.fields[name] = made
            result.hints[name] = field_hints(property_schema, name in required)
    return result


def _tags(union: Schema, defs: Mapping[str, Schema]) -> list[Tag]:
    tags = []
    for option in union["oneOf"]:
        member = resolve(option, defs)
        properties = member.get("properties", {})
        tagged = next(
            (
                (name, property_schema["const"])
                for name, property_schema in properties.items()
                if "const" in property_schema
            ),
            None,
        )
        if tagged is not None:
            name, tag = tagged
            tags.append(Tag(name, tag, title_of(member, tag), properties.get("value", {})))
    return tags


def unrepresentable_fields(schema: Schema, values: Mapping[str, Any]) -> list[str]:
    """What of `values` an `ObjectForm` of `schema` can't show, by title."""
    defs = schema.get("$defs", {})
    form_layout = layout(schema)
    represented = (
        {name.split(NESTED_SEPARATOR, 1)[0] for name in form_layout.fields}
        | set(form_layout.rows)
        | set(form_layout.tagged)
        | set(form_layout.settings_holding_steps)
    )
    found = []
    for name, raw in schema.get("properties", {}).items():
        property_schema = resolve(raw, defs)
        if not is_editable(property_schema) or name in form_layout.settings_holding_steps:
            continue
        title, value = property_schema.get("title", name), values.get(name)
        if name not in represented:
            found.append(title)
        elif name in form_layout.tagged and isinstance(value, dict):
            tagged = form_layout.tagged[name]
            if any((tag := tagged.tag_of(item)) is None or not tag.is_editable for item in value.values()):
                found.append(title)
        elif property_schema.get("type") == "object" and "properties" in property_schema:
            found += unrepresentable_fields(
                {**property_schema, "$defs": defs}, value if isinstance(value, dict) else {}
            )
        elif name in form_layout.rows:
            for row in value if isinstance(value, list) and value else [{}]:
                found += unrepresentable_fields(
                    {**form_layout.rows[name], "$defs": defs}, row if isinstance(row, dict) else {}
                )
        elif (
            property_schema.get("type") == "array"
            and resolve(property_schema.get("items", {}), defs).get("type") == "array"
        ):
            found.append(title)
    return found
