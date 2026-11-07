"""A form of an object's schema, and what it reads of a post: its fields, rows of plain objects and maps of tagged
values, with the core's problems shown on what holds them."""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from django import forms
from gradeflow_core import Problem

from gradeflow_app.ui.forms import BoundField, posted_indexes
from gradeflow_app.ui.schemaforms.fields import field_for
from gradeflow_app.ui.schemaforms.layout import NESTED_SEPARATOR, FieldHints, Layout, Tag, TaggedMap, layout
from gradeflow_app.ui.schemaforms.schema import Schema
from gradeflow_app.ui.schemaforms.widgets import DecimalInput, Segmented

FRACTIONS = {0.5: "½", 0.25: "¼", 0.75: "¾"}
MINUS = "−"  # noqa: RUF001 - a minus sign, as people write it


def preset_label(value: Any) -> str:
    """A preset as people write it: ½ for 0.5, with a minus sign when negative."""
    if isinstance(value, int | float) and value < 0:
        return MINUS + preset_label(-value)
    return FRACTIONS.get(value) or DecimalInput().format_value(value) or str(value)


class HintedField(BoundField):
    """A bound field of an `ObjectForm`, with the core's hints for its template (forms/field.html)."""

    form: ObjectForm

    @property
    def label_controls(self) -> str:
        return self.form.label_controls.get(self.name, "")

    @property
    def replaced_control(self) -> str:
        return self.form.replaced_controls.get(self.name, "")

    @property
    def presets(self) -> list[tuple[str, str, bool]]:
        """Presets: each value as posted, as shown, and whether the field has it."""
        try:
            current = float(self.value())
        except TypeError, ValueError:
            current = None
        return [(str(value), preset_label(value), value == current) for value in self._hints.presets]

    @property
    def is_optional(self) -> bool:
        return not self._hints.required and self.field.initial is None and not self.field.disabled

    @property
    def is_shown(self) -> bool:
        """Whether there is anything to show: a disabled field without a value has nothing."""
        return not self.field.disabled or self.value() not in (None, "", [], {})

    @property
    def visible_when(self) -> str:
        """The fields, by HTML name, and values it is shown for, as JSON for behaviours/forms.js to apply as they
        change."""
        conditions = self._conditions
        return json.dumps({self.form.add_prefix(key): value for key, value in conditions.items()}) if conditions else ""

    @property
    def starts_hidden(self) -> bool:
        return not all(
            any(_matches(self.form[key].value(), option) for option in options)
            for key, options in self._conditions.items()
        )

    @property
    def _hints(self) -> FieldHints:
        return self.form.layout.hints[self.name]

    @property
    def _conditions(self) -> dict[str, Any]:
        """Those on fields of this form; one filled from elsewhere may have any value."""
        return {
            key: value
            for key, value in self._hints.visible_when.items()
            if key in self.form.fields and key not in self.form.replaced_controls
        }


@dataclass(frozen=True)
class MapEntry:
    """An entry of a map of tagged values, as a row."""

    prefix: str
    key: str
    tag: Tag | None
    value: Any
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RowForms:
    """A list of plain objects, each a form, under its heading."""

    name: str
    label: str
    help: str
    forms: list[ObjectForm]
    label_controls: str = ""
    replaced_control: str = ""


@dataclass(frozen=True)
class MapEntries:
    """A map of tagged values, as rows under its heading."""

    name: str
    label: str
    help: str
    map: TaggedMap
    entries: list[MapEntry]
    label_controls: str = ""


class ObjectForm(forms.Form):
    """The fields of one object: a core check, question, adapter or serializer configuration. Its lists of rows,
    maps and settings that hold checks are shown beside its fields, and take the core's problems with what they
    hold."""

    template_name = "forms/object.html"
    bound_field_class = HintedField

    def __init__(
        self,
        schema: Schema,
        initial_object: Mapping[str, Any],
        *,
        defs: Mapping[str, Schema] | None = None,
        path: str = "",
        **kwargs: Any,
    ) -> None:
        defs = schema.get("$defs", {}) if defs is None else defs
        self.layout = layout(schema, defs)
        self.label_controls: dict[str, str] = {}
        """Controls shown beside a field's label, by field name: HTML that the form's owner renders."""
        self.replaced_controls: dict[str, str] = {}
        """What fills a field or list from elsewhere, by name, shown in place of its control: HTML that the form's
        owner renders. Such a field isn't posted, so it keeps its value."""
        self.defs, self.initial_object = defs, initial_object
        initial = {
            name: current for name in self.layout.fields if (current := _nested_value(initial_object, name)) is not None
        }
        # Each check's fields have ids of their own, so a morph from one check to another never keeps a field,
        # with what was typed in it.
        kwargs.setdefault("auto_id", f"id_{path.replace('.', '-')}_%s" if path else "id_%s")
        super().__init__(initial=initial, **kwargs)
        if not self.is_bound:
            self.cleaned_data: dict[str, Any] = {}  # so `add_error` can show the core's problems
        self.fields = dict(self.layout.fields)
        self.rows = [
            RowForms(
                name,
                *self.layout.headings[name],
                [
                    ObjectForm(item, row, defs=defs, prefix=f"{name}-{index}")
                    for index, row in enumerate(initial_object.get(name) or [])
                ],
            )
            for name, item in self.layout.rows.items()
        ]
        self.maps = [
            MapEntries(
                name,
                *self.layout.headings[name],
                tagged,
                [
                    MapEntry(f"{name}-{index}", key, tagged.tag_of(entry), entry.get("value"))
                    for index, (key, entry) in enumerate((initial_object.get(name) or {}).items())
                ],
            )
            for name, tagged in self.layout.tagged.items()
        ]
        self.checks_setting_errors: dict[str, list[str]] = {name: [] for name in self.layout.checks_settings}

    def disable(self) -> None:
        """Show the values without letting them change."""
        for form_field in self.fields.values():
            form_field.disabled = True
        for rows in self.rows:
            for form in rows.forms:
                form.disable()

    def add_label_controls(self, name: str, html: str) -> None:
        """Show `html`, controls of the form's owner, beside the label of the field, list or map `name`."""
        self.label_controls[name] = html
        self.rows = [replace(rows, label_controls=html) if rows.name == name else rows for rows in self.rows]
        self.maps = [
            replace(entries, label_controls=html) if entries.name == name else entries for entries in self.maps
        ]

    def replace_control(self, name: str, html: str) -> None:
        """Show `html` in place of the control of the field or list `name`, which something else fills."""
        self.replaced_controls[name] = html
        self.rows = [replace(rows, replaced_control=html) if rows.name == name else rows for rows in self.rows]

    def __getitem__(self, name: str) -> HintedField:
        bound = super().__getitem__(name)
        assert isinstance(bound, HintedField)
        return bound

    def _field_at(self, location: Sequence[int | str]) -> str | None:
        """The field holding the value at a core error's location, if this form has one."""
        parts = [str(part) for part in location]
        candidates = (NESTED_SEPARATOR.join(parts[:end]) for end in range(len(parts), 0, -1))
        return next((name for name in candidates if name in self.fields), None)

    def add_problems(self, problems: Iterable[Problem]) -> None:
        for problem in problems:
            self.add_problem(problem.field, problem.message)

    def add_problem(self, location: Sequence[int | str], message: str) -> None:
        """Show a problem the core found at `location`, relative to this object, on what holds that value: a field, a
        setting that holds checks, a row or a map's entry. One that nothing here holds, such as a setting only YAML
        can write, shows on the whole form after the setting's name."""
        name, *inner_location = location or [""]
        index = inner_location[0] if inner_location else None
        row_forms = next((rows.forms for rows in self.rows if rows.name == name), [])
        map_entries = next((entries.entries for entries in self.maps if entries.name == name), [])
        if str(name) in self.checks_setting_errors:
            self.checks_setting_errors[str(name)].append(message)
        elif isinstance(index, int) and index < len(row_forms):
            row_forms[index].add_problem(inner_location[1:], message)
        elif entry := next((entry for entry in map_entries if entry.key == str(index)), None):
            entry.errors.append(message)
        elif field := self._field_at(location):
            self.add_error(field, message)
        else:
            named = next((str(part) for part in reversed(location) if isinstance(part, str)), "")
            self.add_error(None, f"{named}: {message}" if named else message)

    @property
    def basic_rows(self) -> list[RowForms]:
        return [rows for rows in self.rows if rows.name not in self.layout.advanced]

    @property
    def advanced_rows(self) -> list[RowForms]:
        return [rows for rows in self.rows if rows.name in self.layout.advanced]

    @property
    def basic_maps(self) -> list[MapEntries]:
        return [entries for entries in self.maps if entries.name not in self.layout.advanced]

    @property
    def advanced_maps(self) -> list[MapEntries]:
        return [entries for entries in self.maps if entries.name in self.layout.advanced]

    @property
    def basic_sections(self) -> list[tuple[str, list[HintedField]]]:
        return self._sections(advanced=False)

    @property
    def advanced_sections(self) -> list[tuple[str, list[HintedField]]]:
        """Advanced fields, under "More options"."""
        return self._sections(advanced=True)

    @property
    def advanced_changed_count(self) -> int:
        """How many advanced fields differ from their defaults, and advanced lists and maps have anything, for the
        closed section's label."""
        changed: int = sum(self[name].initial != self.fields[name].initial for name in self._advanced)
        return (
            changed
            + sum(bool(rows.forms) for rows in self.advanced_rows)
            + sum(bool(entries.entries) for entries in self.advanced_maps)
        )

    @property
    def has_advanced_errors(self) -> bool:
        """Whether an advanced field, row or entry has an error, which must not stay hidden."""
        return (
            any(self[name].errors for name in self._advanced)
            or any(row.errors for rows in self.advanced_rows for row in rows.forms)
            or any(entry.errors for entries in self.advanced_maps for entry in entries.entries)
        )

    @property
    def _advanced(self) -> list[str]:
        return [name for name in self.fields if name in self.layout.advanced]

    def _sections(self, advanced: bool) -> list[tuple[str, list[HintedField]]]:
        """The fields shown, in the schema's order: each group under its label, other fields under none."""
        group_of = {name: label for label, names in self.layout.groups for name in names}
        sections: list[tuple[str, list[HintedField]]] = []
        for name in self.fields:
            if (name in self.layout.advanced) != advanced or not self[name].is_shown:
                continue
            label = group_of.get(name, "")
            if not sections or sections[-1][0] != label:
                sections.append((label, []))
            sections[-1][1].append(self[name])
        return sections

    def values(self) -> dict[str, Any]:
        """The object with what was posted in place. A field posted empty goes to the core empty, never quietly
        replaced by its default; one left out of the post, or that didn't clean, stays as it was."""
        result = dict(self.initial_object)
        for name, value in self.cleaned_data.items():
            if name in self.replaced_controls or self._is_omitted(name):
                continue
            parent, last = _parent_of(result, name)
            parent[last] = value
        posted = {name for name in [*self.layout.rows, *self.layout.tagged] if f"{name}-posted" in self.data}
        for name in posted & self.layout.rows.keys():
            result[name] = _read_rows(self.data, name, self.layout.rows[name], self.defs)
        for name in posted & self.layout.tagged.keys():
            result[name] = _read_tagged(self.data, name, self.layout.tagged[name], self.initial_object.get(name) or {})
        return result

    def _is_omitted(self, name: str) -> bool:
        """Whether the post left the field out, as a client posting only some fields does; an unticked box counts
        as posted."""
        widget = self.fields[name].widget
        return bool(widget.value_omitted_from_data(self.data, self.files, self.add_prefix(name)))


def allow_unset(form: ObjectForm, name: str) -> None:
    """Let the field `name` be left without a value, starting without the one its schema defaults to."""
    field = form.fields.get(name)
    if isinstance(field, forms.BooleanField):
        field = form.fields[name] = forms.NullBooleanField(
            label=field.label,
            help_text=field.help_text,
            widget=Segmented(choices=[("", "No default"), (True, "Yes"), (False, "No")]),
        )
    elif isinstance(field, forms.TypedChoiceField):
        field.choices = [
            ("", "No default"),
            *((value, label) for value, label in cast(list[tuple[Any, str]], field.choices) if value != ""),
        ]
        field.empty_value = None
    if field is not None:
        field.initial = None


def edit_collection(form_layout: Layout, name: str, current: Any, data: Mapping[str, Any]) -> Any:
    if data.get("operation") == "add_row":
        if (tagged := form_layout.tagged.get(name)) is not None:
            key, entry = tagged.new_entry(current or {})
            return {**(current or {}), key: entry}
        if name in form_layout.rows:
            return [*(current or []), {}]
    elif data.get("operation") == "remove_row":
        value = str(data.get("value", ""))
        if isinstance(current, dict):
            removed_key = str(data.get(f"{name}-{value}-key", value)).strip()
            return {entry_key: entry for entry_key, entry in current.items() if entry_key != removed_key}
        if isinstance(current, list) and value.isdigit():
            return [row for index, row in enumerate(current) if index != int(value)]
    return current


def with_row_edit(form: ObjectForm, values: dict[str, Any], data: Mapping[str, Any]) -> dict[str, Any] | None:
    """The form's `values` with the row `data` adds or removes, if it asks for that."""
    if data.get("operation") not in ("add_row", "remove_row"):
        return None
    target = str(data.get("target", ""))
    return {**values, target: edit_collection(form.layout, target, values.get(target), data)}


def _matches(value: Any, expected: Any) -> bool:
    """Whether a field's value is `expected`, compared as the browser compares them (behaviours/forms.js)."""
    return bool(value) is expected if isinstance(expected, bool) else str(value) == str(expected)


def _nested_value(value: Mapping[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split(NESTED_SEPARATOR):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _parent_of(target: dict[str, Any], dotted: str) -> tuple[dict[str, Any], str]:
    """The object holding a nested field, copied on the way down so the form's value is left alone."""
    *parents, last = dotted.split(NESTED_SEPARATOR)
    for part in parents:
        nested = target[part] = dict(target.get(part) or {})
        target = nested
    return target, last


def _read_rows(data: Mapping[str, Any], name: str, schema: Schema, defs: Mapping[str, Schema]) -> list[Any]:
    """The rows of `name` posted as `name-<index>-<field>`, cleaned like an `ObjectForm`."""
    rows = []
    for index in posted_indexes(data, name):
        form = ObjectForm(schema, {}, defs=defs, data=data, prefix=f"{name}-{index}")
        form.full_clean()
        rows.append(form.values())
    return rows


def _read_tagged(data: Mapping[str, Any], name: str, tagged: TaggedMap, current: Mapping[str, Any]) -> dict[str, Any]:
    """A map of tagged values posted as rows of `name-<index>-key`, `-tag` and `-value`. A row posting only its key,
    one the editor can't edit, keeps its `current` entry."""
    by_tag = {tag.value: tag for tag in tagged.editable_tags}
    result = {}
    for index in posted_indexes(data, name):
        key = str(data.get(f"{name}-{index}-key", "")).strip()
        tag = by_tag.get(str(data.get(f"{name}-{index}-tag", "")))
        if key and tag is not None:
            result[key] = {
                tag.field: tag.value,
                "value": _typed_value(data.get(f"{name}-{index}-value", ""), tag.schema),
            }
        elif key in current:
            result[key] = current[key]
    return result


def _typed_value(posted_value: Any, schema: Schema) -> Any:
    form_field = field_for(schema, "", "", {})
    return form_field.to_python(posted_value) if form_field is not None else posted_value
