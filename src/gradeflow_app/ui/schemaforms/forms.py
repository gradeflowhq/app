"""A form of an object's schema, and what it reads of a post: its fields, rows of plain objects and maps of tagged
values, with the core's problems shown on what holds them."""

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, cast

from django import forms
from django.forms.utils import ErrorList
from gradeflow_core import Problem

from gradeflow_app.ui.forms import BoundField, posted_indexes
from gradeflow_app.ui.schemaforms.layout import NESTED, Extras, Layout, Tag, TaggedMap, layout
from gradeflow_app.ui.schemaforms.schema import Schema
from gradeflow_app.ui.schemaforms.widgets import CodeInput, DecimalInput, Segmented

FRACTIONS = {0.5: "½", 0.25: "¼", 0.75: "¾"}
MINUS = "−"  # noqa: RUF001 - a minus sign, as people write it


def preset_label(value: Any) -> str:
    """A quick pick as people write it: ½ for 0.5, with a minus sign when negative."""
    if isinstance(value, int | float) and value < 0:
        return MINUS + preset_label(-value)
    return FRACTIONS.get(value) or DecimalInput().format_value(value) or str(value)


class HintedField(BoundField):
    """A bound field of an `ObjectForm`, with the core's hints for its template (forms/field.html)."""

    form: ObjectForm

    @property
    def addon(self) -> str:
        return self.form.addons.get(self.name, "")

    @property
    def filled(self) -> str:
        return self.form.filled.get(self.name, "")

    @property
    def presets(self) -> list[tuple[str, str, bool]]:
        """Quick picks: each value as posted, as shown, and whether the field has it."""
        try:
            current = float(self.value())
        except TypeError, ValueError:
            current = None
        return [(str(value), preset_label(value), value == current) for value in self._extras.presets]

    @property
    def optional(self) -> bool:
        return not self._extras.required and self.field.initial is None and not self.field.disabled

    @property
    def shown(self) -> bool:
        """Whether there is anything to show: a disabled field without a value has nothing."""
        return not self.field.disabled or self.value() not in (None, "", [], {})

    @property
    def errors(self) -> ErrorList:
        """Django's errors with the field's value, and the core's problems with it."""
        errors: ErrorList = super().errors
        problems = self.form.problems.get(self.name)
        return self.form.error_class([*errors, *problems], renderer=self.form.renderer) if problems else errors

    @property
    def visible_when(self) -> str:
        """The fields, by HTML name, and values it is shown for, as JSON for fields.js to apply as they change."""
        conditions = self._conditions
        return json.dumps({self.form.add_prefix(key): value for key, value in conditions.items()}) if conditions else ""

    @property
    def concealed(self) -> bool:
        return not all(_same(self.form[key].value(), value) for key, value in self._conditions.items())

    @property
    def _extras(self) -> Extras:
        return self.form.layout.extras[self.name]

    @property
    def _conditions(self) -> dict[str, Any]:
        """Those on fields of this form; one filled from elsewhere may have any value."""
        return {
            key: value
            for key, value in self._extras.visible_when.items()
            if key in self.form.fields and key not in self.form.filled
        }


@dataclass(frozen=True)
class Entry:
    """An entry of a map of tagged values, as a row."""

    prefix: str
    key: str
    tag: Tag | None
    value: Any
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rows:
    """A list of plain objects, each a form, under its heading."""

    name: str
    label: str
    help: str
    forms: list[ObjectForm]
    addon: str = ""
    filled: str = ""


@dataclass(frozen=True)
class Entries:
    """A map of tagged values, as rows under its heading."""

    name: str
    label: str
    help: str
    map: TaggedMap
    entries: list[Entry]
    addon: str = ""


class ObjectForm(forms.Form):
    """The fields of one object: a core check, question, adapter or serializer configuration. Its lists of rows,
    maps and slots of checks are shown beside its fields, and take the core's problems with what they hold."""

    template_name = "forms/object.html"
    bound_field_class = HintedField

    def __init__(
        self,
        schema: Schema,
        value: Mapping[str, Any],
        *,
        defs: Mapping[str, Schema] | None = None,
        path: str = "",
        **kwargs: Any,
    ) -> None:
        defs = schema.get("$defs", {}) if defs is None else defs
        self.layout = shape = layout(schema, defs)
        self.addons: dict[str, str] = {}
        """Controls shown beside a field's label, by field name: HTML that the form's owner renders."""
        self.filled: dict[str, str] = {}
        """What fills a field or list from elsewhere, by name, shown in place of its control: HTML that the form's
        owner renders. Such a field isn't posted, so it keeps its value."""
        self.defs, self.value = defs, value
        initial = {name: current for name in shape.fields if (current := _get(value, name)) is not None}
        # Each check's fields have ids of their own, so a morph from one check to another never keeps a field,
        # with what was typed in it.
        kwargs.setdefault("auto_id", f"id_{path.replace('.', '-')}_%s" if path else "id_%s")
        super().__init__(initial=initial, **kwargs)
        self.fields = dict(shape.fields)
        for form_field in self.fields.values():
            if isinstance(form_field.widget, CodeInput):
                form_field.widget.path = path
        self.rows = [
            Rows(
                name,
                *shape.headings[name],
                [
                    ObjectForm(item, row, defs=defs, prefix=f"{name}-{index}")
                    for index, row in enumerate(value.get(name) or [])
                ],
            )
            for name, item in shape.rows.items()
        ]
        self.entries = [
            Entries(
                name,
                *shape.headings[name],
                tagged,
                [
                    Entry(f"{name}-{index}", key, tagged.tag_of(entry), entry.get("value"))
                    for index, (key, entry) in enumerate((value.get(name) or {}).items())
                ],
            )
            for name, tagged in shape.tagged.items()
        ]
        self.slot_errors: dict[str, list[str]] = {name: [] for name in shape.slots}
        self.problems: dict[str | None, list[str]] = {}
        """The core's problems with what this object holds, by field; None for the object itself."""

    def disable(self) -> None:
        """Show the values without letting them change."""
        for each in self.fields.values():
            each.disabled = True
        for rows in self.rows:
            for form in rows.forms:
                form.disable()

    def adorn(self, name: str, html: str) -> None:
        """Show `html`, controls of the form's owner, beside the label of the field, list or map `name`."""
        self.addons[name] = html
        self.rows = [replace(rows, addon=html) if rows.name == name else rows for rows in self.rows]
        self.entries = [replace(entries, addon=html) if entries.name == name else entries for entries in self.entries]

    def fill(self, name: str, html: str) -> None:
        """Show `html` in place of the control of the field or list `name`, which something else fills."""
        self.filled[name] = html
        self.rows = [replace(rows, filled=html) if rows.name == name else rows for rows in self.rows]

    def __getitem__(self, name: str) -> HintedField:
        bound = super().__getitem__(name)
        assert isinstance(bound, HintedField)
        return bound

    def _field_at(self, loc: Sequence[int | str]) -> str | None:
        """The field holding the value at a core error's location, if this form has one."""
        parts = [str(part) for part in loc]
        candidates = (NESTED.join(parts[:end]) for end in range(len(parts), 0, -1))
        return next((name for name in candidates if name in self.fields), None)

    def add_problems(self, problems: Iterable[Problem]) -> None:
        for problem in problems:
            self.add_problem(problem.field, problem.message)

    def add_problem(self, loc: Sequence[int | str], message: str) -> None:
        """Show a problem the core found at `loc`, relative to this object, on what holds that value: a field, a
        slot of checks, a row or a map's entry. One that nothing here holds, such as a setting only YAML can write,
        shows on the whole form after the setting's name."""
        head, *rest = loc or [""]
        index = rest[0] if rest else None
        rows = next((each.forms for each in self.rows if each.name == head), [])
        entries = next((each.entries for each in self.entries if each.name == head), [])
        if str(head) in self.slot_errors:
            self.slot_errors[str(head)].append(message)
        elif isinstance(index, int) and index < len(rows):
            rows[index].add_problem(rest[1:], message)
        elif entry := next((each for each in entries if each.key == str(index)), None):
            entry.errors.append(message)
        elif field := self._field_at(loc):
            self.problems.setdefault(field, []).append(message)
        else:
            named = next((str(part) for part in reversed(loc) if isinstance(part, str)), "")
            self.problems.setdefault(None, []).append(f"{named}: {message}" if named else message)

    def non_field_errors(self) -> ErrorList:
        errors = super().non_field_errors()
        problems = self.problems.get(None)
        return (
            self.error_class([*errors, *problems], error_class="nonfield", renderer=self.renderer)
            if problems
            else errors
        )

    @property
    def basic_rows(self) -> list[Rows]:
        return [each for each in self.rows if each.name not in self.layout.advanced]

    @property
    def more_rows(self) -> list[Rows]:
        return [each for each in self.rows if each.name in self.layout.advanced]

    @property
    def basic_entries(self) -> list[Entries]:
        return [each for each in self.entries if each.name not in self.layout.advanced]

    @property
    def more_entries(self) -> list[Entries]:
        return [each for each in self.entries if each.name in self.layout.advanced]

    @property
    def basic(self) -> list[tuple[str, list[HintedField]]]:
        return self._sections(advanced=False)

    @property
    def more(self) -> list[tuple[str, list[HintedField]]]:
        """Advanced fields, under "More options"."""
        return self._sections(advanced=True)

    @property
    def more_changed(self) -> int:
        """How many advanced fields differ from their defaults, and advanced lists and maps have anything, for the
        closed section's label."""
        changed: int = sum(self[name].initial != self.fields[name].initial for name in self._advanced)
        return (
            changed
            + sum(bool(rows.forms) for rows in self.more_rows)
            + sum(bool(entries.entries) for entries in self.more_entries)
        )

    @property
    def more_open(self) -> bool:
        """Whether an advanced field, row or entry has an error, which must not stay hidden."""
        return (
            any(self[name].errors for name in self._advanced)
            or any(row.errors for each in self.more_rows for row in each.forms)
            or any(entry.errors for each in self.more_entries for entry in each.entries)
        )

    @property
    def _advanced(self) -> list[str]:
        return [name for name in self.fields if name in self.layout.advanced]

    def _sections(self, advanced: bool) -> list[tuple[str, list[HintedField]]]:
        def in_section(name: str) -> bool:
            return (name in self.layout.advanced) == advanced

        in_groups = {name for _, names in self.layout.groups for name in names}

        def shown(name: str) -> bool:
            return in_section(name) and self[name].shown

        loose = [self[name] for name in self.fields if name not in in_groups and shown(name)]
        groups = [(label, [self[name] for name in names if shown(name)]) for label, names in self.layout.groups]
        return [(label, fields) for label, fields in [("", loose), *groups] if fields]

    def values(self) -> dict[str, Any]:
        """The object with what was posted in place. A field posted empty goes to the core empty, never quietly
        replaced by its default; one left out of the post, or that didn't clean, stays as it was."""
        result = dict(self.value)
        for name, value in self.cleaned_data.items():
            if name in self.filled or self._omitted(name):
                continue
            parent, last = _parent(result, name)
            parent[last] = value
        posted = {name for name in [*self.layout.rows, *self.layout.tagged] if f"{name}-present" in self.data}
        for name in posted & self.layout.rows.keys():
            result[name] = _read_rows(self.data, name, self.layout.rows[name], self.defs)
        for name in posted & self.layout.tagged.keys():
            result[name] = _read_tagged(self.data, name, self.layout.tagged[name], self.value.get(name) or {})
        return result

    def _omitted(self, name: str) -> bool:
        """Whether the post left the field out, as a client posting only some fields does; an unticked box counts
        as posted."""
        widget = self.fields[name].widget
        return bool(widget.value_omitted_from_data(self.data, self.files, self.add_prefix(name)))


def allow_unset(form: ObjectForm, name: str) -> None:
    """Let the field `name` be left without a value, starting without the one its schema defaults to."""
    field = form.fields.get(name)
    if isinstance(field, forms.BooleanField):
        field = form.fields[name] = forms.TypedChoiceField(
            label=field.label,
            help_text=field.help_text,
            required=False,
            choices=[("", "No default"), (True, "Yes"), (False, "No")],
            coerce=lambda value: value == "True",
            empty_value=None,
            widget=Segmented,
        )
    elif isinstance(field, forms.TypedChoiceField):
        field.choices = [
            ("", "No default"),
            *((value, label) for value, label in cast(list[tuple[Any, str]], field.choices) if value != ""),
        ]
        field.empty_value = None
    if field is not None:
        field.initial = None


def edit_collection(shape: Layout, name: str, current: Any, data: Mapping[str, Any]) -> Any:
    if data.get("op") == "add_row":
        if (tagged := shape.tagged.get(name)) is not None:
            key, entry = tagged.new_entry(current or {})
            return {**(current or {}), key: entry}
        if name in shape.rows:
            return [*(current or []), {}]
    elif data.get("op") == "remove_row":
        value = str(data.get("value", ""))
        if isinstance(current, dict):
            key = str(data.get(f"{name}-{value}-key", value)).strip()
            return {key_: entry for key_, entry in current.items() if key_ != key}
        if isinstance(current, list) and value.isdigit():
            return [row for index, row in enumerate(current) if index != int(value)]
    return current


def rows_edited(form: ObjectForm, values: dict[str, Any], data: Mapping[str, Any]) -> dict[str, Any] | None:
    """The form's `values` with the row `data` adds or removes, if it asks for that."""
    if data.get("op") not in ("add_row", "remove_row"):
        return None
    target = str(data.get("target", ""))
    return {**values, target: edit_collection(form.layout, target, values.get(target), data)}


def _same(value: Any, expected: Any) -> bool:
    """Whether a field's value is the one a condition expects, or one of a list of them, compared as the browser
    compares them (fields.js)."""
    if isinstance(expected, list):
        return any(_same(value, option) for option in expected)
    return bool(value) is expected if isinstance(expected, bool) else str(value) == str(expected)


def _get(value: Mapping[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split(NESTED):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _parent(target: dict[str, Any], dotted: str) -> tuple[dict[str, Any], str]:
    """The object holding a nested field, copied on the way down so the form's value is left alone."""
    *parents, last = dotted.split(NESTED)
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
            result[key] = {tag.field: tag.value, "value": _typed(data.get(f"{name}-{index}-value", ""), tag.schema)}
        elif key in current:
            result[key] = current[key]
    return result


def _typed(raw: Any, schema: Schema) -> Any:
    match schema.get("type"):
        case "boolean":
            return str(raw).lower() in ("true", "on", "1", "yes")
        case "integer" | "number" if raw in (None, ""):
            return None  # not filled in yet, rather than wrong
        case "integer" | "number" as kind:
            try:
                return int(raw) if kind == "integer" else float(raw)
            except ValueError:
                return raw
        case _:
            return str(raw)
