"""How an object's schema is edited: its fields, groups, rows, maps of tagged values and slots of checks."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from itertools import count
from typing import Any

from django import forms

from gradeflow_app.ui.schemaforms.fields import field_for
from gradeflow_app.ui.schemaforms.schema import Schema, editable, hint, holds_checks, resolve, title_of, unwrap_nullable

NESTED = "__"
"""Joins a nested object's name and its field's in form field names, which become HTML IDs."""

SCALARS = frozenset({"string", "number", "integer", "boolean"})


@dataclass(frozen=True)
class Tag:
    """A member of a union of tagged values: `{field: value, "value": ...}`."""

    field: str
    value: str
    label: str
    schema: Schema

    @property
    def editable(self) -> bool:
        """Whether its value is one typed in; others, like lists, are kept as they are."""
        return self.schema.get("type") in SCALARS


@dataclass(frozen=True)
class TaggedMap:
    """A map of named, tagged values, like a check's parameters."""

    tags: list[Tag]
    item: str
    """What one entry is called."""
    pattern: str = ""
    """What names must look like, as a regular expression."""

    @property
    def editable_tags(self) -> list[Tag]:
        return [tag for tag in self.tags if tag.editable]

    def tag_of(self, entry: object) -> Tag | None:
        return next(
            (tag for tag in self.tags if isinstance(entry, Mapping) and entry.get(tag.field) == tag.value), None
        )

    def new_entry(self, taken: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        """A name not yet `taken`, and an entry of the first type, its value left to fill in."""
        stem = "_".join(self.item.split())
        name = next(name for n in count(len(taken) + 1) if (name := f"{stem}_{n}") not in taken)
        first = self.editable_tags[0]
        return name, {first.field: first.value}


@dataclass(frozen=True)
class Extras:
    """What the core's hints say of a field beyond its type."""

    required: bool = False
    visible_when: Mapping[str, Any] = field(default_factory=dict)
    """Show it only while these fields of the same form have these values."""
    presets: tuple[Any, ...] = ()
    """The core's examples of a number, to pick at a click beside the field."""


def extras_of(schema: Schema, required: bool) -> Extras:
    prop, nullable = unwrap_nullable(schema)
    gradeflow = hint(prop)
    numeric = prop.get("type") in ("number", "integer")
    return Extras(
        required=required and not nullable,
        visible_when=gradeflow.get("applies_when") or {},
        presets=tuple(value for value in schema.get("examples") or () if isinstance(value, int | float))
        if numeric
        else (),
    )


@dataclass
class Layout:
    """How an object's schema is edited."""

    fields: dict[str, forms.Field] = field(default_factory=dict)
    extras: dict[str, Extras] = field(default_factory=dict)
    groups: list[tuple[str, list[str]]] = field(default_factory=list)
    """Labelled fieldsets of nested objects' fields."""
    rows: dict[str, Schema] = field(default_factory=dict)
    """Lists of plain objects by name: their item schema."""
    tagged: dict[str, TaggedMap] = field(default_factory=dict)
    """Maps of tagged values by name."""
    headings: dict[str, tuple[str, str]] = field(default_factory=dict)
    """The label and help of each list of rows and map, by name."""
    slots: list[str] = field(default_factory=list)
    """The names of the fields that hold checks."""
    advanced: set[str] = field(default_factory=set)
    """Fields, rows, maps and groups most people leave as they are, by name."""

    def add_group(self, name: str, label: str, nested: Layout) -> None:
        """A nested object's fields, as `<name>__<field>` fields in a labelled group."""
        names = []
        for inner, made in nested.fields.items():
            extras = nested.extras[inner]
            prefixed = f"{name}{NESTED}{inner}"
            self.fields[prefixed] = made
            self.extras[prefixed] = replace(
                extras, visible_when={f"{name}{NESTED}{key}": value for key, value in extras.visible_when.items()}
            )
            if inner in nested.advanced or name in self.advanced:
                self.advanced.add(prefixed)
            names.append(prefixed)
        self.groups.append((label, names))


def _follows_a_choice(prop: Schema, properties: Mapping[str, Schema], defs: Mapping[str, Schema]) -> bool:
    """Whether a field applies only once another field leaves its default. If so, it shows next to that field instead
    of under More options, unless that field is there too."""
    wanted = hint(prop).get("applies_when") or {}
    choices = [(resolve(properties.get(name, {}), defs), values) for name, values in wanted.items()]
    return bool(wanted) and all(
        not hint(choice).get("advanced")
        and choice.get("default") not in (values if isinstance(values, list) else [values])
        for choice, values in choices
    )


def layout(schema: Schema, defs: Mapping[str, Schema] | None = None) -> Layout:
    """The fields of `schema`, whose references are to `defs`, by default its own."""
    defs = schema.get("$defs", {}) if defs is None else defs
    result = Layout()
    required = set(schema.get("required", ()))
    for name, raw in schema.get("properties", {}).items():
        prop = resolve(raw, defs)
        if not editable(prop):
            continue
        label, help_text = title_of(prop, name), prop.get("description", "")
        if hint(prop).get("advanced") and not _follows_a_choice(prop, schema["properties"], defs):
            result.advanced.add(name)
        if holds_checks(prop):
            result.slots.append(name)
        elif prop.get("type") == "object" and "properties" in prop:
            result.add_group(name, label, layout(prop, defs))
        elif prop.get("type") == "array" and resolve(prop.get("items", {}), defs).get("type") == "object":
            result.rows[name] = resolve(prop["items"], defs)
            result.headings[name] = label, help_text
        elif isinstance(prop.get("additionalProperties"), dict) and "oneOf" in prop["additionalProperties"]:
            tags = _tags(prop["additionalProperties"], defs)
            item = hint(prop).get("item") or "entry"
            result.tagged[name] = TaggedMap(tags, item, prop.get("propertyNames", {}).get("pattern", ""))
            result.headings[name] = label, help_text
        elif (made := field_for(prop, label, help_text, defs)) is not None:
            result.fields[name] = made
            result.extras[name] = extras_of(prop, name in required)
    return result


def _tags(union: Schema, defs: Mapping[str, Schema]) -> list[Tag]:
    tags = []
    for option in union["oneOf"]:
        member = resolve(option, defs)
        properties = member.get("properties", {})
        tagged = next(((name, prop["const"]) for name, prop in properties.items() if "const" in prop), None)
        if tagged is not None:
            name, tag = tagged
            tags.append(Tag(name, tag, title_of(member, tag), properties.get("value", {})))
    return tags


def unrepresentable(schema: Schema, values: Mapping[str, Any]) -> list[str]:
    """What of `values` an `ObjectForm` of `schema` can't show, by title."""
    defs = schema.get("$defs", {})
    shape = layout(schema)
    represented = (
        {name.split("__", 1)[0] for name in shape.fields} | set(shape.rows) | set(shape.tagged) | set(shape.slots)
    )
    found = []
    for name, raw in schema.get("properties", {}).items():
        prop = resolve(raw, defs)
        if not editable(prop) or name in shape.slots:
            continue
        title, value = prop.get("title", name), values.get(name)
        if name not in represented:
            found.append(title)
        elif name in shape.tagged and isinstance(value, dict):
            tagged = shape.tagged[name]
            if any((tag := tagged.tag_of(item)) is None or not tag.editable for item in value.values()):
                found.append(title)
        elif prop.get("type") == "object" and "properties" in prop:
            found += unrepresentable({**prop, "$defs": defs}, value if isinstance(value, dict) else {})
        elif name in shape.rows:
            for row in value if isinstance(value, list) and value else [{}]:
                found += unrepresentable({**shape.rows[name], "$defs": defs}, row if isinstance(row, dict) else {})
        elif prop.get("type") == "array" and resolve(prop.get("items", {}), defs).get("type") == "array":
            found.append(title)
    return found
