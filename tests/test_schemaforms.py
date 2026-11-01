import json
import re
from typing import Any

import pytest
from django.urls import reverse
from django.utils.html import format_html
from gradeflow_core.editing import HINT_KEY

from gradeflow_app.ui.schemaforms.fields import SEGMENTED_UP_TO
from gradeflow_app.ui.schemaforms.forms import ObjectForm
from gradeflow_app.ui.schemaforms.layout import unrepresentable
from gradeflow_app.ui.schemaforms.schema import Schema
from gradeflow_app.ui.schemaforms.widgets import DecimalInput


def obj(**properties: Schema) -> Schema:
    return {"type": "object", "properties": properties}


def hinted(prop: Schema, **hints: Any) -> Schema:
    return {**prop, HINT_KEY: hints}


def names(sections: list[tuple[str, list[Any]]]) -> list[str]:
    return [field.name for _, fields in sections for field in fields]


CHOICE = obj(
    combine={"type": "string", "enum": ["all", "sum"], "default": "all"},
    out_of=hinted({"type": "number"}, advanced=True, applies_when={"combine": "sum"}),
    strict=hinted({"type": "boolean", "default": False}, advanced=True, applies_when={"combine": "all"}),
    points={"type": "array", "items": {"type": "string"}},
    whole=hinted({"type": "boolean", "default": False}, advanced=True),
)


def test_rarely_changed_options_wait_under_more_options() -> None:
    form = ObjectForm(CHOICE, {})
    assert names(form.basic) == ["combine", "out_of", "points"], "beside the choice it follows"
    assert names(form.more) == ["strict", "whole"], "strict applies by default"
    assert (form.more_changed, form.more_open) == (0, False)
    assert ObjectForm(CHOICE, {"whole": True}).more_changed == 1


def test_a_field_following_a_rarely_changed_choice_waits_with_it() -> None:
    schema = obj(
        decide=hinted({"type": "string", "enum": ["fast", "careful"], "default": "fast"}, advanced=True),
        effort=hinted({"type": "string"}, advanced=True, applies_when={"decide": "careful"}),
    )
    form = ObjectForm(schema, {})
    assert (names(form.basic), names(form.more)) == ([], ["decide", "effort"])


def test_a_field_shows_only_while_its_condition_holds() -> None:
    shown = ObjectForm(CHOICE, {"combine": "sum"}, prefix="check")
    hidden = ObjectForm(CHOICE, {"combine": "all"})
    assert json.loads(shown["out_of"].visible_when) == {"check-combine": "sum"}
    assert not shown["out_of"].concealed
    assert hidden["out_of"].concealed


@pytest.mark.parametrize(("match", "concealed"), [("exact", True), ("spelling", False), ("meaning", False)])
def test_a_condition_listing_values_holds_for_any_of_them(match: str, concealed: bool) -> None:
    schema = obj(
        match={"type": "string", "enum": ["exact", "spelling", "meaning"]},
        near=hinted({"type": "number"}, applies_when={"match": ["spelling", "meaning"]}),
    )
    form = ObjectForm(schema, {"match": match}, prefix="check")
    assert json.loads(form["near"].visible_when) == {"check-match": ["spelling", "meaning"]}
    assert form["near"].concealed is concealed


def test_choices_read_as_the_core_labels_them_or_as_written() -> None:
    schema = obj(option=hinted({"type": "string", "enum": ["mark", "mRNA"]}, labels={"mark": "Mark it"}))
    choices = dict(ObjectForm(schema, {}).fields["option"].choices)  # type: ignore[attr-defined]
    assert choices == {"mark": "Mark it", "mRNA": "mRNA"}


def test_a_read_only_form_leaves_out_fields_without_a_value() -> None:
    form = ObjectForm(CHOICE, {"points": []})
    form.disable()
    assert names(form.basic) == ["combine"]


def test_a_long_list_of_choices_starts_with_none_chosen_unless_one_is_the_default() -> None:
    keys = [f"Q{n}" for n in range(1, 8)]
    unset = {"type": "string", "title": "Question", "enum": keys, "default": ""}
    schema = {"type": "object", "properties": {"question": unset, "set": {**unset, "default": "Q3"}}}
    form = ObjectForm(schema, {})
    assert form.fields["question"].choices[0] == ("", "Choose question")  # type: ignore[attr-defined]
    assert form.fields["set"].choices[0] == ("Q1", "Q1")  # type: ignore[attr-defined]


def test_numbers_read_as_people_write_them() -> None:
    widget = DecimalInput()
    assert [widget.format_value(value) for value in (2.0, 1e-06, 12.57)] == ["2", "0.000001", "12.57"]


def test_core_errors_land_on_the_field_holding_their_value() -> None:
    schema = obj(
        evaluate={"type": "boolean"},
        setup=obj(time_limit={"type": "number"}),
        answers={"type": "array", "items": {"type": "string"}},
    )
    form = ObjectForm(schema, {})
    for loc in [("evaluate",), ("setup", "time_limit"), ("answers", 0), (), ("elsewhere",)]:
        form.add_problem(loc, "Wrong.")
    assert form.problems == {
        "evaluate": ["Wrong."],
        "setup__time_limit": ["Wrong."],
        "answers": ["Wrong."],
        None: ["Wrong.", "elsewhere: Wrong."],
    }


def test_a_field_left_out_of_a_post_stays_as_it_was() -> None:
    schema = obj(
        types={"type": "array", "items": {"type": "string"}},
        separator={"type": "string", "default": ","},
        reading=obj(trim={"type": "boolean", "default": True}),
    )
    posted = ObjectForm(schema, {"separator": ";"}, data={"types": ["text"]})
    assert posted.is_valid()
    assert posted.values() == {
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
    prop = {"type": "string", HINT_KEY: {"kind": kind}}
    form = ObjectForm({"type": "object", "properties": {"value": prop}}, {}, data={"value": value})
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

    def kept_as(path: str, code: str) -> str:
        found = re.search(r'id="([^"]+)" hx-morph-skip-children', str(ObjectForm(schema, {"code": code}, path=path)))
        assert found is not None
        return found[1]

    assert kept_as("", "a") == kept_as("", "b"), "typing keeps the editor"
    assert kept_as("", "a") != kept_as("with.checks.0", "a"), "another check's code gets its own editor"


def test_a_choice_of_numbers_is_posted_back_as_a_number() -> None:
    schema = {"type": "object", "properties": {"value": {"type": "integer", "enum": [1, 2], "title": "Value"}}}
    form = ObjectForm(schema, {}, data={"value": "2"})
    assert form.is_valid()
    assert form.values() == {"value": 2}


def test_numbers_offer_quick_picks_as_people_write_them() -> None:
    schema = obj(
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
        schema = obj(choice={"type": "string", "enum": [f"{label}{n}" for n in range(count)]})
        return ObjectForm(schema, {})["choice"].field.widget

    assert widget(SEGMENTED_UP_TO).template_name.endswith("segmented.html")
    assert widget(SEGMENTED_UP_TO + 1).input_type == "select"
    assert widget(SEGMENTED_UP_TO, "Neighbours in order ").input_type == "select", "too long for one row"


def test_checks_held_in_a_check_are_shown_as_cards() -> None:
    check = hinted({"type": "object"}, kind="check")
    schema = obj(check=check, checks={"type": "array", "items": check})
    assert unrepresentable(schema, {"check": {"uses": "a"}, "checks": [{"uses": "b"}]}) == []


def test_what_fills_a_field_shows_in_its_place_and_the_post_leaves_it_alone() -> None:
    value = {"combine": "sum", "points": ["light"], "strict": True}
    form = ObjectForm(CHOICE, value, data={"combine": "all", "points": ["sun"]})
    for name in ("combine", "points", "strict"):
        form.fill(name, format_html('<p id="filled-{}"></p>', name))
    page = str(form)
    assert all(f'id="filled-{name}"' in page for name in ("combine", "points", "strict"))
    assert not re.search(r'name="(combine|points|strict)"', page), "whatever its control"
    assert form.is_valid()
    assert form.values() == value | {"whole": False}


def test_a_field_shown_for_a_value_filled_elsewhere_always_shows() -> None:
    form = ObjectForm(CHOICE, {"combine": "all"})
    form.fill("combine", "<p></p>")
    assert (form["out_of"].visible_when, form["out_of"].concealed) == ("", False)


def test_a_whole_number_stays_whole() -> None:
    form = ObjectForm(obj(out_of={"type": "number"}), {}, data={"out_of": "2"})
    assert form.is_valid()
    assert repr(form.values()["out_of"]) == "2"


def test_quick_picks_are_only_the_examples_that_are_numbers() -> None:
    form = ObjectForm(obj(out_of={"type": "number", "examples": [2, "abc", 1.5]}), {})
    assert [value for value, _, _ in form["out_of"].presets] == ["2", "1.5"]
