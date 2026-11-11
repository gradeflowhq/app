import json
import re
from typing import Any

import pytest
from django.core.exceptions import NON_FIELD_ERRORS
from django.urls import reverse
from django.utils.html import format_html
from gradeflow_core.editing import HINT_KEY

from gradeflow_app.ui.schemaforms.fields import MAX_SEGMENTS
from gradeflow_app.ui.schemaforms.forms import ObjectForm
from gradeflow_app.ui.schemaforms.layout import unrepresentable_fields
from gradeflow_app.ui.schemaforms.schema import Schema
from gradeflow_app.ui.schemaforms.widgets import DecimalInput


def object_schema(**properties: Schema) -> Schema:
    return {"type": "object", "properties": properties}


def hinted(property_schema: Schema, **hints: Any) -> Schema:
    return {**property_schema, HINT_KEY: hints}


def names(sections: list[tuple[str, list[Any]]]) -> list[str]:
    return [field.name for _, fields in sections for field in fields]


CHECK_SCHEMA = object_schema(
    combine={"type": "string", "enum": ["all", "sum"], "default": "all"},
    max_marks=hinted({"type": "number"}, advanced=True, applies_when={"combine": ["sum"]}),
    strict=hinted({"type": "boolean", "default": False}, advanced=True, applies_when={"combine": ["all"]}),
    points={"type": "array", "items": {"type": "string"}},
    whole_words=hinted({"type": "boolean", "default": False}, advanced=True),
)


def test_rarely_changed_options_wait_under_more_options() -> None:
    form = ObjectForm(CHECK_SCHEMA, {})
    assert names(form.basic_sections) == ["combine", "max_marks", "points"], "beside the choice it follows"
    assert names(form.advanced_sections) == ["strict", "whole_words"], "strict applies by default"
    assert (form.advanced_changed_count, form.has_advanced_errors) == (0, False)
    assert ObjectForm(CHECK_SCHEMA, {"whole_words": True}).advanced_changed_count == 1


def test_groups_keep_their_place_among_the_fields() -> None:
    setup = hinted(object_schema(code={"type": "string", "default": ""}), advanced=True)
    schema = object_schema(
        setup=setup,
        expected=hinted({"type": "boolean", "default": True}, advanced=True),
        name=hinted({"type": "string"}, advanced=True),
    )
    assert [label for label, _ in ObjectForm(schema, {}).advanced_sections] == ["Setup", ""]


def test_a_field_following_a_rarely_changed_choice_waits_with_it() -> None:
    schema = object_schema(
        model=hinted({"type": "string", "enum": ["fast", "careful"], "default": "fast"}, advanced=True),
        careful_effort=hinted({"type": "string"}, advanced=True, applies_when={"model": ["careful"]}),
    )
    form = ObjectForm(schema, {})
    assert (names(form.basic_sections), names(form.advanced_sections)) == ([], ["model", "careful_effort"])


def test_a_field_shows_only_while_its_condition_holds() -> None:
    shown = ObjectForm(CHECK_SCHEMA, {"combine": "sum"}, prefix="check")
    hidden = ObjectForm(CHECK_SCHEMA, {"combine": "all"})
    assert json.loads(shown["max_marks"].visible_when) == {"check-combine": ["sum"]}
    assert not shown["max_marks"].starts_hidden
    assert hidden["max_marks"].starts_hidden


@pytest.mark.parametrize(("match", "starts_hidden"), [("exact", True), ("spelling", False), ("meaning", False)])
def test_a_condition_listing_values_holds_for_any_of_them(match: str, starts_hidden: bool) -> None:
    schema = object_schema(
        match={"type": "string", "enum": ["exact", "spelling", "meaning"]},
        threshold=hinted({"type": "number"}, applies_when={"match": ["spelling", "meaning"]}),
    )
    form = ObjectForm(schema, {"match": match}, prefix="check")
    assert json.loads(form["threshold"].visible_when) == {"check-match": ["spelling", "meaning"]}
    assert form["threshold"].starts_hidden is starts_hidden


def test_a_field_hidden_until_its_condition_holds_still_points_at_its_help_and_errors() -> None:
    schema = object_schema(
        combine={"type": "string", "enum": ["all", "sum"]},
        max_marks=hinted({"type": "number", "description": "At most this many."}, applies_when={"combine": ["sum"]}),
    )
    form = ObjectForm(schema, {"combine": "all"})
    form.add_error("max_marks", "Must be 0 or more.")
    assert form["max_marks"].starts_hidden
    assert 'aria-describedby="id_max_marks_helptext id_max_marks_error"' in str(form["max_marks"])


def test_a_group_of_choices_points_at_its_errors() -> None:
    form = ObjectForm(CHECK_SCHEMA, {})
    form.add_error("combine", "Choose one.")
    assert '<fieldset class="grid gap-1.5" aria-describedby="id_combine_error">' in form["combine"].as_field_group()


def test_choices_read_as_the_core_labels_them_or_as_written() -> None:
    schema = object_schema(option=hinted({"type": "string", "enum": ["mark", "mRNA"]}, labels={"mark": "Mark it"}))
    choices = dict(ObjectForm(schema, {}).fields["option"].choices)  # type: ignore[attr-defined]
    assert choices == {"mark": "Mark it", "mRNA": "mRNA"}


def test_a_read_only_form_leaves_out_fields_without_a_value() -> None:
    form = ObjectForm(CHECK_SCHEMA, {"points": []})
    form.disable()
    assert names(form.basic_sections) == ["combine"]


def test_a_long_list_of_choices_starts_with_none_chosen_unless_one_is_the_default() -> None:
    keys = [f"Q{number}" for number in range(1, 8)]
    unset = {"type": "string", "title": "Question", "enum": keys, "default": ""}
    schema = {"type": "object", "properties": {"question": unset, "preset_question": {**unset, "default": "Q3"}}}
    form = ObjectForm(schema, {})
    assert form.fields["question"].choices[0] == ("", "Choose question")  # type: ignore[attr-defined]
    assert form.fields["preset_question"].choices[0] == ("Q1", "Q1")  # type: ignore[attr-defined]


def test_numbers_read_as_people_write_them() -> None:
    widget = DecimalInput()
    assert [widget.format_value(value) for value in (2.0, 1e-06, 12.57)] == ["2", "0.000001", "12.57"]


def test_core_errors_land_on_the_field_holding_their_value() -> None:
    schema = object_schema(
        evaluate={"type": "boolean"},
        setup=object_schema(time_limit={"type": "number"}),
        answers={"type": "array", "items": {"type": "string"}},
    )
    form = ObjectForm(schema, {})
    for field in ["/evaluate", "/setup/time_limit", "/answers/0", "", "/elsewhere"]:
        form.add_problem(field, "Wrong.")
    assert form.errors == {
        "evaluate": ["Wrong."],
        "setup__time_limit": ["Wrong."],
        "answers": ["Wrong."],
        NON_FIELD_ERRORS: ["Wrong.", "elsewhere: Wrong."],
    }


def test_a_field_left_out_of_a_post_stays_as_it_was() -> None:
    schema = object_schema(
        types={"type": "array", "items": {"type": "string"}},
        separator={"type": "string", "default": ","},
        reading=object_schema(trim={"type": "boolean", "default": True}),
    )
    form = ObjectForm(schema, {"separator": ";"}, data={"types": ["text"]})
    assert form.is_valid()
    assert form.values() == {
        "types": ["text"],
        "separator": ";",
        "reading": {"trim": False},
    }, "an unticked box is never posted, so it counts as unticked"


def test_the_core_alone_says_what_is_required() -> None:
    schema = {
        "type": "object",
        "properties": {"count": {"type": "integer"}, "name": {"type": "string"}, "note": {"type": "string"}},
        "required": ["count", "name"],
    }
    form = ObjectForm(schema, {"count": 3, "name": "Ann", "kept": True}, data={"count": "", "name": ""})
    assert form.is_valid()
    assert form.values() == {"count": None, "name": "", "kept": True}, "cleared fields go to the core empty"
    page = str(form)
    assert " required" not in page
    assert page.count("(optional)") == 1, "only the field the core doesn't require"


@pytest.mark.parametrize("kind", ["", "markdown", "code"])
@pytest.mark.parametrize("value", [" ", "  a phrase\n"])
def test_generated_text_fields_preserve_whitespace(kind: str, value: str) -> None:
    property_schema = {"type": "string", HINT_KEY: {"kind": kind}}
    form = ObjectForm({"type": "object", "properties": {"value": property_schema}}, {}, data={"value": value})
    assert form.is_valid()
    assert form.values() == {"value": value}


def test_markdown_fields_have_formatting_buttons_and_a_preview() -> None:
    schema = {"type": "object", "properties": {"about": {"type": "string", HINT_KEY: {"kind": "markdown"}}}}
    page = str(ObjectForm(schema, {"about": "**Hi**"}))
    assert '<markdown-toolbar for="id_about"' in page
    assert f'data-preview="{reverse("markdown_preview")}"' in page
    assert ">**Hi**</textarea>" in page


def test_a_code_editor_outlives_redraws_of_its_own_field_only() -> None:
    schema = {"type": "object", "properties": {"code": {"type": "string", HINT_KEY: {"kind": "code"}}}}

    def editor_id(path: str, code: str) -> str:
        found = re.search(r'id="([^"]+)" hx-morph-skip-children', str(ObjectForm(schema, {"code": code}, path=path)))
        assert found is not None
        return found[1]

    assert editor_id("", "a") == editor_id("", "b"), "typing keeps the editor"
    assert editor_id("", "a") != editor_id("/with/steps/0", "a"), "another check's code gets its own editor"


def test_a_choice_of_numbers_is_posted_back_as_a_number() -> None:
    schema = {"type": "object", "properties": {"value": {"type": "integer", "enum": [1, 2], "title": "Value"}}}
    form = ObjectForm(schema, {}, data={"value": "2"})
    assert form.is_valid()
    assert form.values() == {"value": 2}


def test_numbers_offer_quick_picks_as_people_write_them() -> None:
    schema = object_schema(
        tolerance={"type": "number", "examples": [0.01, 0.05, 0.1]},
        weight={"type": "number", "default": 1, "examples": [2, 1, 0.5, 0.25, -0.5, -1]},
    )
    form = ObjectForm(schema, {"tolerance": 0.05})
    assert form["tolerance"].presets == [("0.01", "0.01", False), ("0.05", "0.05", True), ("0.1", "0.1", False)]
    weight = form["weight"]
    assert [label for _, label, _ in weight.presets] == ["2", "1", "½", "¼", "−½", "−1"]  # noqa: RUF001
    assert [chosen for *_, chosen in weight.presets] == [False, True, False, False, False, False], "1 by default"
    assert 'data-preset="0.5"' in str(weight.as_field_group())


def test_a_few_short_choices_sit_side_by_side_and_others_are_listed() -> None:
    def widget(count: int, label: str = "c") -> Any:
        schema = object_schema(choice={"type": "string", "enum": [f"{label}{index}" for index in range(count)]})
        return ObjectForm(schema, {})["choice"].field.widget

    assert widget(MAX_SEGMENTS).template_name.endswith("segmented.html")
    assert widget(MAX_SEGMENTS + 1).input_type == "select"
    assert widget(MAX_SEGMENTS, "Neighbours in order ").input_type == "select", "too long for one row"


def test_steps_held_in_a_step_are_shown_as_cards() -> None:
    step = hinted({"type": "object"}, kind="step")
    schema = object_schema(step=step, steps={"type": "array", "items": step})
    assert unrepresentable_fields(schema, {"step": {"uses": "a"}, "steps": [{"uses": "b"}]}) == []


def test_what_fills_a_field_shows_in_its_place_and_the_post_leaves_it_alone() -> None:
    value = {"combine": "sum", "points": ["light"], "strict": True}
    form = ObjectForm(CHECK_SCHEMA, value, data={"combine": "all", "points": ["sun"]})
    for name in ("combine", "points", "strict"):
        form.replace_control(name, format_html('<p id="filled-{}"></p>', name))
    page = str(form)
    assert all(f'id="filled-{name}"' in page for name in ("combine", "points", "strict"))
    assert not re.search(r'name="(combine|points|strict)"', page), "whatever its control"
    assert form.is_valid()
    assert form.values() == value | {"whole_words": False}


def test_a_field_shown_for_a_value_filled_elsewhere_always_shows() -> None:
    form = ObjectForm(CHECK_SCHEMA, {"combine": "all"})
    form.replace_control("combine", "<p></p>")
    assert (form["max_marks"].visible_when, form["max_marks"].starts_hidden) == ("", False)


def test_a_whole_number_stays_whole() -> None:
    form = ObjectForm(object_schema(max_marks={"type": "number"}), {}, data={"max_marks": "2"})
    assert form.is_valid()
    assert repr(form.values()["max_marks"]) == "2"


def test_quick_picks_are_only_the_examples_that_are_numbers() -> None:
    form = ObjectForm(object_schema(max_marks={"type": "number", "examples": [2, "abc", 1.5]}), {})
    assert [value for value, _, _ in form["max_marks"].presets] == ["2", "1.5"]
