"""The method designer: a person's draft of a method, or a published version they try, beside their test answers."""

import html
import re
from html import unescape
from typing import TYPE_CHECKING

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape
from gradeflow_core import BUILTINS, Question
from gradeflow_core.editing import MethodDraft, dump_yaml
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.jobs.runs import COMPLETED, UNREADABLE_REASON, RunState
from gradeflow_app.libraries.designer.runs import run_state
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.method_tests import MethodTests
from gradeflow_app.libraries.models import Method, MethodDraftRecord, VersionTests
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.questions.models import QUESTION_TYPE_LABELS
from tests.conftest import (
    HTMX_HEADERS,
    FakeRunner,
    create_course,
    designer_record,
    designer_url,
    method_definition,
    post_callback,
    post_publish,
    post_to_designer,
    publish_version,
    signed_in_client,
)

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as Response

pytestmark = pytest.mark.django_db


@pytest.fixture
def course(user: User) -> Course:
    return create_course(user, "Method laboratory")


@pytest.fixture
def method(client: Client, course: Course) -> Method:
    """A new method in the course's library, `method-laboratory`, its draft marking text answers with the keyword
    `light`."""
    response = client.post(new_method_url(course), new_method_fields("Light"))
    assert response.status_code == 302
    created = Method.objects.get(library__course=course)
    assert post_to_designer(client, created, "method_yaml", definition=method_definition()).status_code == 200
    return created


def new_method_url(course: Course) -> str:
    return reverse("method_new", query={"course": course.pk})


def new_method_fields(name: str) -> dict[str, str]:
    return {"label": name, "summary": "Says light.", "question_type": "text"}


def draft(method: Method) -> MethodDraftRecord:
    return MethodDraftRecord.objects.get(method=method)


def saved_tests(method: Method, number: int | None = None) -> MethodTests:
    tests: MethodTests = designer_record(method, number).tests
    return tests


def core_draft(method: Method) -> MethodDraft:
    """The method's draft as the core reads it."""
    return MethodWorkspace(method, draft(method)).draft


def input_name_at(core: MethodDraft, path: str) -> str | None:
    return next(field.bound_input for field in core.configurable_fields() if field.path == path)


def answer_rows(method: Method, number: int | None = None) -> list[tuple[str, bool]]:
    return [(row["answers"]["Q1"], row["is_example"]) for row in saved_tests(method, number).get("rows", [])]


def enabled_fields(page: Response) -> list[str]:
    """The fields of a page's marking form that can be changed."""
    form = page.context["editor"].form
    return [name for name, field in form.fields.items() if not field.disabled]


def test_a_new_method_starts_as_a_draft(client: Client, course: Course, method: Method) -> None:
    listing = client.get(reverse("course_methods", args=[course.pk])).text
    assert f'hx-post="{escape(new_method_url(course))}"' in listing, "New method is a dialog on the Methods page"
    assert "Save to" not in listing
    assert method.library == course.library
    page = client.get(designer_url(method, "method_page")).text
    assert "Light" in page
    assert "Draft" in page
    assert "read-only-fields" not in page
    assert ">Methods</a></span>" not in page
    assert ">Publish</button>" in page
    assert not method.versions.exists()
    listing = client.get(reverse("course_methods", args=[course.pk])).text
    assert "Says light." in listing, "a draft says what it does too"
    assert reverse("method_latest", args=[method.pk]).endswith("/")
    assert client.get(reverse("method_latest", args=[method.pk])).headers["Location"] == designer_url(
        method, "method_page"
    )


def test_test_answers_are_added_changed_and_removed_whole(client: Client, method: Method) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    post_to_designer(client, method, "method_answers", operation="add", answer="dark")
    assert answer_rows(method) == [("light", False), ("dark", False)]
    page = client.get(designer_url(method, "method_page")).text
    assert re.search(r'aria-label="Answer of submission 2"[^>]*>dark</textarea>', page)
    post_to_designer(client, method, "method_answers", operation="save", row="1", answer="sunlight")
    post_to_designer(client, method, "method_answers", operation="example", row="0")
    assert answer_rows(method) == [("light", True), ("sunlight", False)]
    post_to_designer(client, method, "method_answers", operation="clear", row="1")
    assert answer_rows(method) == [("light", True), ("", False)]
    post_to_designer(client, method, "method_answers", operation="delete", row="0")
    assert answer_rows(method) == [("", False)]
    response = post_to_designer(client, method, "method_answers", operation="delete", row="5")
    assert "This answer is no longer there." in response.text


def test_chosen_answers_become_the_methods_example(client: Client, method: Method) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    post_to_designer(client, method, "method_answers", operation="example", row="0")
    [example] = core_draft(method).examples
    assert [row.answers for row in example.rows] == [{"Q1": "light"}]
    assert saved_tests(method)["example_index"] == 0


def test_running_the_tests_shows_the_marks_until_the_answers_change(
    client: Client, method: Method, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    post_to_designer(client, method, "method_answers", operation="add", answer="dark")
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method, "method_run")
    page = client.get(designer_url(method, "method_page")).text
    tests = draft(method)
    assert run_state(tests) == (COMPLETED, False), tests.failure
    assert [row["answers"] for row in tests.results or []] == [{"Q1": "light"}, {"Q1": "dark"}], "a result per answer"
    assert "Results are up to date" in page
    results = client.get(designer_url(method, "method_results")).text
    assert "Results are up to date" in results
    post_to_designer(client, method, "method_check", operation="open", target="with.checks.0")
    assert "Results are up to date" in client.get(designer_url(method, "method_results")).text, "only looked"
    post_to_designer(client, method, "method_answers", operation="add", answer="lights")
    assert "Changed since the last run" in client.get(designer_url(method, "method_results")).text


def test_test_answers_show_how_each_check_marked_them(
    client: Client, method_with_length_check: Method, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    post_to_designer(client, method_with_length_check, "method_answers", operation="add", answer="dark")
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method_with_length_check, "method_run")
    results = client.get(designer_url(method_with_length_check, "method_results")).text
    assert "Breakdown" in results
    assert "Includes <code>light</code>." in results
    assert "Any length." in results


def test_unreadable_test_results_fail_the_run(
    client: Client,
    method: Method,
    idle_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method, "method_run")
    [run_id] = idle_runner.run_ids("draft-tests")
    assert post_callback(run_id, {"event": "finished", "outcome": [{"answers": {"Q1": "light"}}]}) == 204
    assert run_state(draft(method))[0] == RunState("failed", UNREADABLE_REASON)
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method, "method_run")
    rerun = idle_runner.run_ids("draft-tests")[-1]
    other = {"answers": {"Q1": "other"}, "matches_expected": None, "results": {}, "max_marks": 1}
    assert post_callback(rerun, {"event": "finished", "outcome": [other]}) == 204
    tests = draft(method)
    assert run_state(tests)[0] == RunState("failed", tests.failure)
    assert "don't match the test answers" in tests.failure


FIRST_POINTS_PATH, SECOND_POINTS_PATH = "with.checks.0.with.points", "with.checks.1.with.points"


def test_a_setting_is_filled_from_an_input_or_fixed(client: Client, method: Method) -> None:
    two_checks = dump_yaml(
        {
            "name": "Light",
            "summary": "Says light.",
            "question_types": ["text"],
            "checks": [
                {"uses": "gradeflow/key-points@1", "with": {"points": ["light"]}},
                {"uses": "gradeflow/key-points@1", "with": {"points": ["sun"]}},
            ],
        }
    )
    post_to_designer(client, method, "method_yaml", definition=two_checks)
    post_to_designer(client, method, "method_field", operation="new_input", field=FIRST_POINTS_PATH)
    name = input_name_at(core_draft(method), FIRST_POINTS_PATH)
    assert name in core_draft(method).inputs
    post_to_designer(client, method, "method_field", operation="bind", field=SECOND_POINTS_PATH, input=name)
    assert input_name_at(core_draft(method), SECOND_POINTS_PATH) == name
    post_to_designer(client, method, "method_field", operation="unbind", field=FIRST_POINTS_PATH)
    post_to_designer(client, method, "method_field", operation="unbind", field=SECOND_POINTS_PATH)
    core = core_draft(method)
    assert (input_name_at(core, FIRST_POINTS_PATH), input_name_at(core, SECOND_POINTS_PATH), core.inputs) == (
        None,
        None,
        {},
    )


def menu_items(page: str) -> list[tuple[str, bool]]:
    """The choices of the menus beside settings an input can fill, and whether each is what fills it."""
    found = re.findall(r'role="menuitemradio" aria-checked="(true|false)"[^>]*>\s*([^<]+?)\s*<', page)
    return [(label, checked == "true") for checked, label in found]


def test_a_settings_menu_marks_what_fills_it(client: Client, method: Method) -> None:
    first_check_query = {"path": "with.checks.0"}
    page = client.get(designer_url(method, "method_page"), first_check_query).text
    assert menu_items(page)[0] == ("Fixed value", True)
    post_to_designer(client, method, "method_field", operation="new_input", field=FIRST_POINTS_PATH)
    page_with_input = client.get(designer_url(method, "method_page"), first_check_query).text
    [declaration] = core_draft(method).inputs.values()
    assert menu_items(page_with_input)[:2] == [("Fixed value", False), (declaration["title"], True)]
    assert page_with_input.count("New input") == page.count("New input") - 1


def test_checks_of_a_method_share_an_input(client: Client, method: Method) -> None:
    name = new_input(client, method, FIRST_POINTS_PATH)
    post_to_designer(
        client,
        method,
        "method_check",
        path="",
        operation="add",
        target="with.checks.1",
        method="gradeflow/key-points@1",
    )
    post_to_designer(client, method, "method_field", operation="bind", field=SECOND_POINTS_PATH, input=name)
    assert input_name_at(core_draft(method), SECOND_POINTS_PATH) == name
    input_page = input_form(client, method, name)
    label = BUILTINS.find("gradeflow/key-points@1").label
    assert f"{label} 1 · Points" in input_page
    assert f"{label} 2 · Points" in input_page
    post_to_designer(client, method, "method_field", operation="new_input", field=SECOND_POINTS_PATH)
    core = core_draft(method)
    second_input = input_name_at(core, SECOND_POINTS_PATH)
    assert second_input not in (None, name), "an input of its own"
    assert set(core.inputs) == {name, second_input}


def test_an_inputs_label_help_and_default_are_saved(client: Client, method: Method) -> None:
    post_to_designer(client, method, "method_field", operation="new_input", field=FIRST_POINTS_PATH)
    name = input_name_at(core_draft(method), FIRST_POINTS_PATH)
    assert name is not None
    tab = client.get(designer_url(method, "method_inputs"), {"input": name, "tab": "inputs"})
    assert 'name="input-default"' in tab.text
    details = {"input": name, "input-title": "Words", "input-description": "What answers must say."}
    post_to_designer(client, method, "method_inputs", None, **details | {"input-default": ["sun"]})
    assert core_draft(method).inputs[name] == {
        "title": "Words",
        "description": "What answers must say.",
        "default": ["sun"],
    }
    post_to_designer(client, method, "method_values", None, **{f"values-{name}": ["moon"]})
    assert saved_tests(method)["test_values"] == {name: ["moon"]}
    post_to_designer(client, method, "method_values", operation="reset")
    assert saved_tests(method)["test_values"] == {}


def test_a_checks_problems_show_while_an_input_has_only_a_test_value(client: Client, method: Method) -> None:
    definition = dump_yaml(
        {
            "label": "Light",
            "summary": "Says light.",
            "question_types": ["text"],
            "inputs": {"points": {"title": "Points"}},
            "checks": [
                {"uses": "gradeflow/key-points@1", "with": {"points": "{{ inputs.points }}"}},
                {"uses": "gradeflow/equivalent-expression@1", "with": {"answers": ["x2"]}},
            ],
        }
    )
    post_to_designer(client, method, "method_yaml", definition=definition)
    page = client.get(designer_url(method, "method_page"))
    assert page.context["problems"] == ["Give “Points” a test value or a default."]
    post_to_designer(client, method, "method_values", None, **{"values-points": ["sun"]})
    page = client.get(designer_url(method, "method_page"))
    assert (page.context["first_problem_check"], page.context["problems"]) == ("with.checks.1", [])


def test_every_part_of_an_inputs_declaration_is_edited(client: Client, method: Method) -> None:
    combine_path = "with.checks.0.with.combine"
    post_to_designer(client, method, "method_field", operation="new_input", field=combine_path)
    name = input_name_at(core_draft(method), combine_path)
    assert name is not None
    tab = client.get(designer_url(method, "method_inputs"), {"input": name, "tab": "inputs"}).text
    assert 'name="input-advanced"' in tab
    assert 'name="input-choices"' in tab
    fields = {"input": name, "input-title": "Combine", "input-default": "any", "input-advanced": "True"}
    post_to_designer(client, method, "method_inputs", None, **fields | {"input-choices": ["all", "any"]})
    assert core_draft(method).inputs[name] == {
        "title": "Combine",
        "default": "any",
        "advanced": True,
        "choices": ["all", "any"],
    }
    cleared_fields: dict[str, object] = {
        "input": name,
        "input-title": "Combine",
        "input-default": "",
        "input-advanced": "",
    }
    post_to_designer(client, method, "method_inputs", None, **cleared_fields)
    assert core_draft(method).inputs[name] == {"title": "Combine"}


MATCH_PATH, WHOLE_WORDS_PATH = (f"with.checks.0.with.{key}" for key in ("match", "whole_words"))
MIN_LENGTH_PATH = "with.checks.1.with.min_length"


@pytest.fixture
def method_with_length_check(client: Client, method: Method) -> Method:
    """`method` with a second check, on the answer's length."""
    checks = [{"uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}, {"uses": "gradeflow/length@1"}]
    text = dump_yaml({"label": "Light", "summary": "Says light.", "question_types": ["text"], "checks": checks})
    assert post_to_designer(client, method, "method_yaml", definition=text).status_code == 200
    return method


def new_input(client: Client, method: Method, field: str) -> str:
    post_to_designer(client, method, "method_field", operation="new_input", field=field)
    name = input_name_at(core_draft(method), field)
    assert name is not None
    return name


def input_form(client: Client, method: Method, name: str) -> str:
    return client.get(designer_url(method, "method_inputs"), {"input": name, "tab": "inputs"}).text


def input_settings_form(page: str) -> str:
    """The input form of a page showing one input."""
    found = re.search(r'<form class="grid gap-4[^"]*" hx-post="[^"]*/inputs/">(.*?)</form>', page, re.DOTALL)
    assert found
    return found.group(1)


def options_of(page: str, name: str) -> list[tuple[str, str]]:
    """The options of the input form's `name` control, by value and label: its buttons, or a list's options."""
    if listed := re.search(rf'<select name="input-{name}".*?</select>', page, re.DOTALL):
        return re.findall(r'<option value="([^"]*)"[^>]*>\s*([^<]+?)\s*<', listed.group(0))
    return re.findall(rf'name="input-{name}" value="([^"]*)"[^>]*>\s*(?:<span[^>]*>)?\s*([^<]+?)\s*<', page)


def field_error(page: str, name: str) -> str:
    found = re.search(rf'id="id_input-{name}_error">(.*?)</div>', page, re.DOTALL)
    return unescape(re.sub(r"<[^>]+>", "", found.group(1))).strip() if found else ""


def test_an_inputs_default_comes_before_advanced_when_it_is_a_table(client: Client, method: Method) -> None:
    check = {"uses": "gradeflow/custom-code@1", "with": {"code": "correct = True\n"}}
    text = dump_yaml({"label": "Light", "summary": "Code.", "question_types": ["text"], "checks": [check]})
    post_to_designer(client, method, "method_yaml", definition=text)
    page = input_form(client, method, new_input(client, method, "with.checks.0.with.parameters"))
    assert page.index('name="default-posted"') < page.index('name="input-advanced"')


def test_an_inputs_choices_are_the_options_of_the_setting_it_fills(client: Client, method: Method) -> None:
    name = new_input(client, method, MATCH_PATH)
    page = input_form(client, method, name)
    options = [("exact", "Exactly"), ("spelling", "Similar spelling"), ("meaning", "Same meaning")]
    assert options_of(page, "choices") == options
    assert 'name="input-examples"' not in page, "a setting with fixed options has nothing to suggest"
    fields: dict[str, object] = {
        "input": name,
        "input-title": "Match",
        "input-choices": ["exact", "spelling"],
        "input-default": "spelling",
    }
    post_to_designer(client, method, "method_inputs", None, **fields)
    assert core_draft(method).inputs[name] == {
        "title": "Match",
        "default": "spelling",
        "choices": ["exact", "spelling"],
    }
    page = input_form(client, method, name)
    assert [value for value, _ in options_of(page, "default")] == ["", "exact", "spelling"]
    listing = client.get(designer_url(method, "method_inputs"), {"tab": "inputs"}).text
    assert "Default: Similar spelling" in listing


def test_choices_and_examples_show_only_where_they_apply(client: Client, method_with_length_check: Method) -> None:
    for field in (FIRST_POINTS_PATH, WHOLE_WORDS_PATH):
        page = input_form(client, method_with_length_check, new_input(client, method_with_length_check, field))
        assert 'name="input-choices"' not in page
        assert 'name="input-examples"' not in page
    name = new_input(client, method_with_length_check, MIN_LENGTH_PATH)
    assert 'name="input-choices"' not in input_form(client, method_with_length_check, name)
    post_to_designer(
        client, method_with_length_check, "method_inputs", None, **{"input": name, "input-examples": ["2", "5"]}
    )
    assert core_draft(method_with_length_check).inputs[name]["examples"] == [2, 5]


def test_a_default_outside_the_choices_shows_on_the_default_with_what_was_typed(client: Client, method: Method) -> None:
    name = new_input(client, method, MATCH_PATH)
    before = core_draft(method).inputs[name]
    fields: dict[str, object] = {
        "input": name,
        "input-title": "How to match",
        "input-choices": ["exact"],
        "input-default": "spelling",
    }
    page = post_to_designer(client, method, "method_inputs", None, **fields).text
    assert core_draft(method).inputs[name] == before
    assert field_error(page, "default") == "Similar spelling isn't one of the choices."
    assert 'value="How to match"' in page
    assert re.search(r'name="input-choices" value="exact"[^>]*checked', page)
    assert not re.search(r'name="input-choices" value="spelling"[^>]*checked', page)


def test_advanced_is_on_or_off_and_leaves_the_default_in_view(client: Client, method: Method) -> None:
    name = new_input(client, method, FIRST_POINTS_PATH)
    page = input_form(client, method, name)
    assert re.search(r'type="checkbox" name="input-advanced"(?![^>]*checked)', page)
    fields: dict[str, object] = {"input": name, "input-title": "Points", "input-default": ["sun"]}
    post_to_designer(client, method, "method_inputs", None, **fields | {"input-advanced": "on"})
    assert core_draft(method).inputs[name]["advanced"] is True
    page = input_form(client, method, name)
    assert "More options" not in page, "the input's own default is never folded away"
    post_to_designer(client, method, "method_inputs", None, **fields)
    assert "advanced" not in core_draft(method).inputs[name], "off, as the setting it fills is"
    whole_words_input = new_input(client, method, WHOLE_WORDS_PATH)
    page = input_form(client, method, whole_words_input)
    assert re.search(r'type="checkbox" name="input-advanced"[^>]*checked', page), "as the setting it fills is"
    assert "More options" not in page


def test_a_yes_or_no_input_may_have_no_default(client: Client, method: Method) -> None:
    name = new_input(client, method, WHOLE_WORDS_PATH)
    for default, saved in (("True", {"default": True}), ("", {})):
        post_to_designer(
            client, method, "method_inputs", None, **{"input": name, "input-default": default, "input-advanced": "on"}
        )
        assert core_draft(method).inputs[name] == {"title": "Whole words only", **saved}


def test_an_edit_of_a_check_leaves_what_inputs_fill(client: Client, method: Method) -> None:
    points_input, match_input, whole_words_input = (
        new_input(client, method, field) for field in (FIRST_POINTS_PATH, MATCH_PATH, WHOLE_WORDS_PATH)
    )
    fields: dict[str, object] = {
        "input": match_input,
        "input-choices": ["exact", "spelling"],
        "input-default": "spelling",
    }
    post_to_designer(client, method, "method_inputs", None, **fields)
    post_to_designer(client, method, "method_inputs", None, **{"input": whole_words_input, "input-default": "True"})
    before = core_draft(method).inputs
    page = client.get(designer_url(method, "method_page"), {"path": "with.checks.0"}).text
    for key in ("points", "match", "whole_words"):
        assert f'id="input-fill-with.checks.0.with.{key}"' in page
        assert f'name="{key}"' not in page
    assert 'Default <span class="text-base-content">Similar spelling</span>' in page
    post_to_designer(
        client, method, "method_check", path="with.checks.0", fields="1", strictness="lenient", match="meaning"
    )
    assert core_draft(method).inputs == before
    assert before[points_input]["default"] == ["light"]


def test_examples_are_typed_like_the_setting(client: Client, method_with_length_check: Method) -> None:
    name = new_input(client, method_with_length_check, MIN_LENGTH_PATH)
    page = post_to_designer(
        client, method_with_length_check, "method_inputs", None, **{"input": name, "input-examples": ["2", "abc"]}
    )
    assert "examples" not in core_draft(method_with_length_check).inputs[name]
    text = page.text
    assert field_error(text, "examples") == "Must be a whole number."
    assert '<option value="abc" selected>' in text


def test_the_default_offers_the_settings_examples_not_the_inputs(
    client: Client, method_with_length_check: Method
) -> None:
    name = new_input(client, method_with_length_check, MIN_LENGTH_PATH)
    post_to_designer(
        client, method_with_length_check, "method_inputs", None, **{"input": name, "input-examples": ["7"]}
    )
    assert 'data-preset="7"' not in input_settings_form(input_form(client, method_with_length_check, name))
    assert 'data-preset="7"' in client.get(designer_url(method_with_length_check, "method_page")).text, (
        "where it's filled in"
    )


def test_a_whole_number_default_is_saved_whole(client: Client, method_with_length_check: Method) -> None:
    name = new_input(client, method_with_length_check, MIN_LENGTH_PATH)
    post_to_designer(client, method_with_length_check, "method_inputs", None, **{"input": name, "input-default": "2"})
    assert repr(core_draft(method_with_length_check).inputs[name]["default"]) == "2"


def test_the_default_explains_the_setting_beside_the_inputs_description(client: Client, method: Method) -> None:
    name = new_input(client, method, WHOLE_WORDS_PATH)
    post_to_designer(
        client, method, "method_inputs", None, **{"input": name, "input-description": "How close it must be."}
    )
    page = input_settings_form(input_form(client, method, name))
    assert page.count("How close it must be.") == 1
    core = core_draft(method)
    field = next(field for field in core.configurable_fields() if field.path == WHOLE_WORDS_PATH)
    setting = core.checks.schema(field.check)["properties"][field.key]
    assert html.unescape(setting["description"]) in html.unescape(page)


def test_a_check_named_by_an_input_shows_the_name_it_gets(client: Client, method: Method) -> None:
    name = new_input(client, method, "with.checks.0.name")

    def shown(path: str) -> str:
        page = client.get(designer_url(method, "method_page"), {"path": path}).text
        assert "{{ inputs." not in page
        return page

    label = BUILTINS.find("gradeflow/key-points@1").label
    assert f">{label}</h3>" in shown("with.checks.0"), "without a default, as its method"
    post_to_designer(client, method, "method_inputs", None, **{"input": name, "input-default": "Says light"})
    assert ">Says light</button>" in shown("")
    assert ">Says light</h3>" in shown("with.checks.0")
    assert "Says light · Name" in input_form(client, method, name)


def test_an_inputs_rows_are_added_and_removed_before_saving(client: Client, method: Method) -> None:
    check = {"uses": "gradeflow/code-tests@1", "with": {"tests": [{"expression": "f(1)", "expected": "2"}]}}
    code_tests_yaml = dump_yaml(
        {"name": "Light", "summary": "Says light.", "question_types": ["text"], "checks": [check]}
    )
    post_to_designer(client, method, "method_yaml", definition=code_tests_yaml)
    tests = "with.checks.0.with.tests"
    post_to_designer(client, method, "method_field", operation="new_input", field=tests)
    name = input_name_at(core_draft(method), tests)
    assert name is not None
    row: dict[str, object] = {
        "input": name,
        "default-posted": "1",
        "default-0-expression": "f(1)",
        "default-0-expected": "2",
    }
    page = post_to_designer(
        client, method, "method_inputs", None, **row | {"operation": "add_row", "target": "default"}
    )
    assert 'name="default-1-expression"' in page.text
    page = post_to_designer(
        client, method, "method_inputs", None, **row | {"operation": "remove_row", "target": "default", "value": "0"}
    )
    assert 'name="default-0-expression"' not in page.text
    assert core_draft(method).inputs[name]["default"] == [{"expression": "f(1)", "expected": "2"}], "saved only on Save"


def test_questions_are_added_and_removed_with_their_answers(client: Client, method: Method) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    post_to_designer(client, method, "method_question", save="1", type="number", max_marks="2")
    tests = saved_tests(method)
    assert list(tests["questions"]) == ["Q1", "Q2"]
    assert tests["rows"] == [{"answers": {"Q1": "light", "Q2": ""}, "is_example": False}]
    post_to_designer(client, method, "method_question", key="Q2", operation="remove")
    tests = saved_tests(method)
    assert list(tests["questions"]) == ["Q1"]
    assert tests["rows"] == [{"answers": {"Q1": "light"}, "is_example": False}]


def test_a_second_question_keeps_the_checks_on_the_first(client: Client, method: Method) -> None:
    post_to_designer(client, method, "method_question", save="1", type="number", max_marks="2")
    core = core_draft(method)
    assert core.question is None
    assert {core.checks.data["with"]["checks"][0].get("question")} == {"Q1"}
    assert core.problems == []


def set_question_in_tests(method: Method, question: dict[str, object]) -> None:
    """Make Q1 of the method's tests `question`."""
    method_draft = draft(method)
    method_draft.tests["questions"]["Q1"] = question
    method_draft.save()


def question_in_tests(method: Method) -> Question:
    """Q1 of the method's tests, as the core reads it."""
    return Question.load(saved_tests(method)["questions"]["Q1"])


def test_an_answer_of_several_values_is_written_value_by_value(client: Client, method: Method) -> None:
    set_question_in_tests(
        method, {"type": "values", "value_types": ["number", "number"], "reading": {"separator": "~"}}
    )
    question = question_in_tests(method)
    schema = question.answer_schema()
    titles = {name: escape(property_schema["title"]) for name, property_schema in schema["properties"].items()}
    page = client.get(designer_url(method, "method_page"), headers=HTMX_HEADERS).text
    assert all(f'placeholder="{title}"' in page for title in titles.values())
    typed = [dict(zip(titles, values, strict=True)) for values in (("2", " -3 "), ("", " "))]
    for values in typed:
        post_to_designer(
            client,
            method,
            "method_answers",
            None,
            **{"operation": "add", **{f"value-{name}": value for name, value in values.items()}},
        )
    written = [question.answer_from(values) for values in typed]
    assert answer_rows(method) == [(answer, False) for answer in written]
    page = client.get(designer_url(method, "method_page"), headers=HTMX_HEADERS).text
    for name, value in question.answer_values(written[0]).items():
        assert f'title="{titles[name]}">{escape(value)}</span>' in page
        assert f'name="value-{name}" value="{escape(value)}"' in page


def test_a_choice_answer_is_ticked_from_the_options(client: Client, method: Method) -> None:
    set_question_in_tests(method, {"type": "choice", "options": ["A", "B", "C"], "allow_multiple": True})
    question = question_in_tests(method)
    post_to_designer(client, method, "method_answers", None, **{"operation": "add", "value-options": ["c", "a"]})
    post_to_designer(client, method, "method_answers", operation="add")
    assert answer_rows(method) == [(question.answer_from({"options": ["c", "a"]}), False), ("", False)]
    post_to_designer(
        client, method, "method_answers", None, **{"operation": "save", "row": "0", "value-options": ["b"]}
    )
    assert answer_rows(method)[0] == (question.answer_from({"options": ["b"]}), False)


def test_an_answer_of_several_options_shows_each_option(client: Client, method: Method) -> None:
    set_question_in_tests(method, {"type": "choice", "options": ["a", "b", "c"], "allow_multiple": True})
    post_to_designer(client, method, "method_answers", None, **{"operation": "add", "value-options": ["c", "a"]})
    page = client.get(designer_url(method, "method_page"), headers=HTMX_HEADERS).text
    shown = re.findall(r'<span class="badge[^"]*" title="[^"]+">([^<]*)</span>', page)
    assert shown == ["a", "c"]


def test_a_text_answer_is_written_whole(client: Client, method: Method) -> None:
    page = client.get(designer_url(method, "method_page"), headers=HTMX_HEADERS).text
    assert 'name="value-' not in page
    post_to_designer(client, method, "method_answers", operation="add", answer="light\nand heat")
    assert answer_rows(method) == [("light\nand heat", False)]


def test_a_draft_is_listed_as_it_says_it_is_so_far(client: Client, method: Method, course: Course) -> None:
    post_to_designer(client, method, "method_yaml", definition="label: Half done\nsummary: Not yet.\nchecks: []\n")
    listed = client.get(reverse("course_methods", args=[course.pk])).text
    text = QUESTION_TYPE_LABELS["text"]
    assert re.search(rf">Half done</a>.*Not yet\.</p></td>\s*<td[^>]*>{re.escape(text)}</td>", listed, re.DOTALL)


def test_details_are_saved(client: Client, method: Method) -> None:
    details_dialog = client.get(designer_url(method, "method_details")).text
    assert 'name="summary"' in details_dialog
    assert "<markdown-toolbar" in details_dialog, "the guide is written as Markdown"
    post_to_designer(client, method, "method_details", label="Bright", summary="Says bright.", question_types=["text"])
    tree = definition_data(draft(method).definition)
    assert (tree["label"], tree["summary"]) == ("Bright", "Says bright.")


def test_details_saved_as_shown_leave_the_definition_as_it_was(client: Client, method: Method) -> None:
    post_to_designer(
        client, method, "method_yaml", definition="label: All\nsummary: Any.\nquestion_types: all\nchecks: []\n"
    )
    before = draft(method).definition
    details_dialog = client.get(designer_url(method, "method_details")).text
    shown = {name: value for name, value in core_draft(method).metadata.items() if value}
    assert all(re.search(f'value="{kind}"[^>]* checked', details_dialog) for kind in shown["question_types"])
    post_to_designer(client, method, "method_details", **shown)
    assert draft(method).definition == before


def test_yaml_the_core_cant_read_leaves_the_draft_as_it_was(client: Client, method: Method) -> None:
    before = draft(method).definition
    response = post_to_designer(client, method, "method_yaml", definition="checks: [")
    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert draft(method).definition == before
    assert (
        post_to_designer(client, method, "method_yaml", definition=method_definition(answer="sun")).status_code == 200
    )
    assert "sun" in draft(method).definition


def test_an_edit_from_an_older_page_changes_nothing(client: Client, method: Method) -> None:
    method_draft = designer_record(method, None)
    response = client.post(
        designer_url(method, "method_answers"),
        {"revision": method_draft.revision - 1, "operation": "add", "answer": "x"},
        headers=HTMX_HEADERS,
    )
    assert response.status_code == 409
    assert "This method changed in another tab." in response.text
    publish_response = client.post(designer_url(method, "method_publish"), {"revision": method_draft.revision - 1})
    assert publish_response.status_code == 409
    assert answer_rows(method) == []
    assert not method.versions.exists()


def test_publishing_makes_the_next_version_and_ends_the_draft(client: Client, method: Method) -> None:
    response = client.post(designer_url(method, "method_publish"), {"revision": designer_record(method, None).revision})
    assert response.headers["Location"] == designer_url(method, "method_page", 1)
    assert not MethodDraftRecord.objects.filter(method=method).exists()
    method.refresh_from_db()
    assert method.next_version_number == 2
    published_url = reverse("method_latest", args=[method.pk])
    assert client.get(designer_url(method, "method_page")).headers["Location"] == published_url
    assert client.get(published_url).headers["Location"] == designer_url(method, "method_page", 1)
    assert client.post(reverse("method_edit", args=[method.pk])).status_code == 302
    post_to_designer(client, method, "method_yaml", definition=method_definition(answer="sun"))
    post_publish(client, method)
    assert list(method.versions.values_list("number", flat=True)) == [2, 1]
    assert "sun" not in method.versions.get(number=1).definition


def test_publishing_records_what_the_tests_gave_the_examples(
    client: Client, method: Method, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    post_to_designer(client, method, "method_answers", operation="add", answer="dark")
    post_to_designer(client, method, "method_answers", operation="example", row="1")
    assert "Run the tests to publish" in client.get(designer_url(method, "method_page")).text
    assert "Run the tests first" in post_publish(client, method).text
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method, "method_run")
    results = client.get(designer_url(method, "method_results")).text
    assert re.search(r'id="method-publish" hx-swap-oob="outerMorph".*?>Publish</button>', results, re.DOTALL)
    assert "Run the tests to publish" not in results
    results_given = draft(method).results
    assert results_given is not None
    dark_result = results_given[1]
    post_publish(client, method)
    assert "Results are up to date" in client.get(designer_url(method, "method_page", 1)).text, "as tested"
    version = method.versions.get()
    published_method = BUILTINS.with_files([(method.full_name, version.definition)]).find(version.ref)
    [row] = published_method.examples[0].rows
    assert row.answers == {"Q1": "dark"}
    dark_q1_result = dark_result["results"]["Q1"]
    assert {key: expected.model_dump() for key, expected in row.expected.items()} == {
        "Q1": {"marks": dark_q1_result["marks"], "feedback": dark_q1_result["feedback"]}
    }
    guide = client.get(reverse("method_guide", args=["method-laboratory/light@1"]), headers=HTMX_HEADERS).text
    assert escape(dark_q1_result["feedback"]) in guide
    client.post(reverse("method_edit", args=[method.pk]))
    post_to_designer(client, method, "method_answers", operation="add", answer="sun")
    assert "No changes to publish" in client.get(designer_url(method, "method_page")).text


def test_a_draft_that_changes_nothing_isnt_published(client: Client, method: Method) -> None:
    post_publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    page = client.get(designer_url(method, "method_page")).text
    assert "No changes to publish" in page
    assert "Nothing changed since version 1." in post_publish(client, method).text
    assert method.versions.count() == 1
    post_to_designer(client, method, "method_yaml", definition=method_definition(answer="sun"))
    assert "No changes to publish" not in client.get(designer_url(method, "method_page")).text


def test_a_publish_that_fails_keeps_the_test_run_and_the_draft(
    client: Client,
    method: Method,
    bo: User,
    idle_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method, "method_run")
    post_to_designer(client, method, "method_answers", operation="add", answer="dark")
    before = draft(method).definition
    publish_version(method, bo, method_definition(answer="newer"))
    with django_capture_on_commit_callbacks(execute=True):
        assert "A newer version was published" in post_publish(client, method).text
    assert run_state(draft(method))[0] == RunState("queued")
    assert draft(method).definition == before


def test_a_draft_with_problems_isnt_published(client: Client, method: Method) -> None:
    post_to_designer(client, method, "method_yaml", definition=method_definition(uses="gradeflow/nope@1"))
    page = post_publish(client, method).text
    assert 'role="alert"' in page
    assert "gradeflow/nope@1" in page
    assert not method.versions.exists()
    assert MethodDraftRecord.objects.filter(method=method).exists()


def test_discarding_a_draft_keeps_what_was_published(client: Client, method: Method) -> None:
    post_publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    response = client.post(designer_url(method, "method_discard"))
    assert response.headers["Location"] == reverse("method_latest", args=[method.pk])
    assert method.versions.count() == 1
    assert not MethodDraftRecord.objects.exists()


def test_discarding_an_unpublished_draft_discards_the_method(
    client: Client, course: Course, method: Method, bo: User
) -> None:
    Member.objects.create(course=course, user=bo, role=Role.EDITOR)
    MethodDraftRecord.objects.create(method=method, user=bo, definition=draft(method).definition)
    client.post(designer_url(method, "method_discard"))
    assert Method.objects.filter(pk=method.pk).exists(), "another person's draft keeps it"
    response = signed_in_client(bo).post(designer_url(method, "method_discard"))
    assert response.headers["Location"] == reverse("course_methods", args=[course.pk])
    assert not Method.objects.exists()


def test_a_version_is_tried_on_ones_own_answers_and_reset(
    client: Client,
    course: Course,
    method: Method,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
    bo: User,
) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    post_publish(client, method)
    page = client.get(designer_url(method, "method_page", 1)).text
    assert "Version 1" in page
    assert ">Edit</button>" in page
    post_to_designer(client, method, "method_answers", 1, operation="add", answer="dark")
    assert answer_rows(method, 1) == [("light", False), ("dark", False)]
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method, "method_run", 1)
    assert run_state(designer_record(method, 1)) == (COMPLETED, False)
    assert post_to_designer(client, method, "method_answers", 1, operation="example", row="0").status_code == 404
    assert post_to_designer(client, method, "method_yaml", 1, definition="checks: [").status_code == 200
    version = method.versions.get()
    assert version.tests["rows"] == [{"answers": {"Q1": "light"}, "is_example": False}]
    post_to_designer(client, method, "method_answers", 1, operation="reset")
    assert answer_rows(method, 1) == [("light", False)]
    Member.objects.create(course=course, user=bo, role=Role.EDITOR)
    signed_in_client(bo).get(designer_url(method, "method_page", 1))
    assert VersionTests.objects.get(user=bo).tests == version.tests, "each person tries it on their own"


def test_viewers_try_versions_but_change_no_method(client: Client, course: Course, method: Method, bo: User) -> None:
    post_publish(client, method)
    Member.objects.create(course=course, user=bo, role=Role.VIEWER)
    viewer = signed_in_client(bo)
    page = viewer.get(designer_url(method, "method_page", 1))
    assert page.status_code == 200
    assert ">Edit</button>" not in page.text
    assert "Add answer" in page.text
    revision = VersionTests.objects.get(user=bo).revision
    response = viewer.post(
        designer_url(method, "method_answers", 1), {"revision": revision, "operation": "add", "answer": "x"}
    )
    assert response.status_code == 200
    assert [row["answers"] for row in VersionTests.objects.get(user=bo).tests["rows"]] == [{"Q1": "x"}]
    revision = VersionTests.objects.get(user=bo).revision
    values = viewer.post(designer_url(method, "method_values", 1), {"revision": revision, "operation": "reset"})
    assert values.status_code == 404, "a version's input values are those it was published with"
    assert viewer.post(reverse("method_edit", args=[method.pk])).status_code == 403
    client.post(reverse("method_edit", args=[method.pk]))
    revision = designer_record(method, None).revision
    assert (
        viewer.post(designer_url(method, "method_answers"), {"revision": revision, "operation": "add"}).status_code
        == 404
    )
    assert viewer.get(designer_url(method, "method_page")).headers["Location"] == reverse(
        "method_latest", args=[method.pk]
    )
    assert MethodDraftRecord.objects.filter(user=bo).count() == 0


def test_a_version_is_read_only_and_lists_the_others(client: Client, method: Method) -> None:
    post_publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    post_to_designer(client, method, "method_yaml", definition=method_definition(answer="sun"))
    post_publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    version_page = client.get(designer_url(method, "method_page", 1))
    page = version_page.text
    for target in (
        designer_url(method, "method_page"),
        designer_url(method, "method_page", 2),
        designer_url(method, "method_page", 1),
    ):
        assert f'<a href="{target}" hx-boost="false"' in page
    assert ">Open draft</button>" in page
    definition = MethodDraftRecord.objects.get(method=method).definition
    removed = post_to_designer(client, method, "method_inputs", 1, input="points", operation="remove")
    assert removed.status_code == 404
    assert MethodDraftRecord.objects.get(method=method).definition == definition
    assert enabled_fields(version_page) == []
    assert 'class="flex flex-1 flex-col read-only-fields"' in page
    draft_page = client.get(designer_url(method, "method_page"), {"path": "with.checks.0"})
    assert "points" in enabled_fields(draft_page)


def test_editing_an_older_version_starts_from_it_on_top_of_the_latest(client: Client, method: Method) -> None:
    post_publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    post_to_designer(client, method, "method_yaml", definition=method_definition(answer="sun"))
    post_publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]), {"number": "1"})
    started = draft(method)
    assert started.based_on == method.versions.get(number=2)
    assert "sun" not in started.definition
    assert post_publish(client, method).headers["HX-Redirect"] == designer_url(method, "method_page", 3)
    assert (
        definition_data(method.versions.get(number=3).definition)["checks"]
        == definition_data(method.versions.get(number=1).definition)["checks"]
    )


def test_editing_needs_a_published_version(client: Client, method: Method) -> None:
    assert client.post(reverse("method_edit", args=[method.pk])).status_code == 404


def test_a_row_that_isnt_a_number_is_a_missing_row(client: Client, method: Method) -> None:
    response = post_to_designer(client, method, "method_answers", operation="delete", row="first")
    assert response.status_code == 200
    assert "This answer is no longer there." in response.text


def test_a_checks_method_is_chosen_from_the_picker(client: Client, method: Method) -> None:
    first_check_query = {"path": "with.checks.0"}
    assert (
        'operation=change_method&amp;target=with.checks.0"'
        in client.get(designer_url(method, "method_page"), first_check_query).text
    )
    assert 'operation=change_method&amp;target="' not in client.get(designer_url(method, "method_page")).text
    picker = client.get(
        designer_url(method, "method_choices"), {"operation": "change_method", "target": "with.checks.0"}
    )
    accepted = BUILTINS.find("gradeflow/accepted-answers@1")
    assert escape(accepted.label) in picker.text
    post_to_designer(
        client,
        method,
        "method_check",
        path="with.checks.0",
        operation="change_method",
        target="with.checks.0",
        method="gradeflow/accepted-answers@1",
    )
    assert definition_data(draft(method).definition)["checks"][0]["uses"] == "gradeflow/accepted-answers@1"


def test_a_version_has_no_draft_only_views(client: Client, method: Method) -> None:
    post_publish(client, method)
    for name in ("method_choices", "method_field", "method_publish", "method_discard"):
        assert client.post(designer_url(method, name)).status_code in (404, 405), name


def test_a_run_of_answers_since_changed_is_out_of_date(
    client: Client,
    method: Method,
    idle_runner: FakeRunner,
    django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks,
) -> None:
    post_to_designer(client, method, "method_answers", operation="add", answer="light")
    with django_capture_on_commit_callbacks(execute=True):
        post_to_designer(client, method, "method_run")
    [run_id] = idle_runner.run_ids("draft-tests")
    post_to_designer(client, method, "method_answers", operation="add", answer="dark")
    assert run_state(draft(method))[1], "the tests changed since it started"
    assert post_callback(run_id, {"event": "finished", "outcome": []}) == 204
    assert (draft(method).run_revision, draft(method).failure) == (None, "")
    assert not idle_runner.held


def test_a_new_method_from_your_methods_is_yours(client: Client, user: User) -> None:
    listing = client.get(reverse("your_methods")).text
    assert f'hx-post="{reverse("method_new")}"' in listing
    response = client.post(reverse("method_new"), new_method_fields("Mine"), headers=HTMX_HEADERS)
    method = Method.objects.get()
    assert response.headers["HX-Redirect"] == designer_url(method, "method_page")
    assert method.full_name == "ana-example-edu/mine"
    assert client.get(reverse("method_new")).status_code == 405


def test_a_new_method_isnt_called_what_another_is(client: Client, course: Course, method: Method) -> None:
    post_publish(client, method)
    response = client.post(new_method_url(course), new_method_fields("light"), headers=HTMX_HEADERS)
    assert response.status_code == 422
    assert "This library already has a method called “Light”." in response.text
    assert re.search(r'name="label"[^>]*aria-invalid="true"', response.text), "on the name field"
    assert Method.objects.count() == 1


def test_an_unpublished_method_is_only_its_authors(client: Client, course: Course, method: Method, bo: User) -> None:
    Member.objects.create(course=course, user=bo, role=Role.EDITOR)
    published_url = reverse("method_latest", args=[method.pk])
    assert client.get(published_url).headers["Location"] == designer_url(method, "method_page")
    assert signed_in_client(bo).get(published_url).status_code == 404


def test_the_picker_for_a_check_thats_gone_isnt_found(client: Client, method: Method) -> None:
    assert (
        client.get(
            designer_url(method, "method_choices"), {"operation": "change_method", "target": "with.checks.5"}
        ).status_code
        == 404
    )
