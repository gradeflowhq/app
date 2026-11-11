"""The method designer: a person's draft of a method, or a published version they try, beside their test answers."""

import html
import re
from html import unescape
from typing import TYPE_CHECKING, Any

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


def setting_name_at(method_draft: MethodDraft, path: str) -> str | None:
    return next(field.bound_setting for field in method_draft.configurable_fields() if field.path == path)


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
    answers = [row["sheet"]["answers"] for row in tests.results or []]
    assert answers == [{"Q1": "light"}, {"Q1": "dark"}], "a result per answer"
    assert "Results are up to date" in page
    flat_results = [{**row["sheet"], "results": row["results"], "max_marks": 1} for row in tests.results or []]
    MethodDraftRecord.objects.filter(pk=tests.pk).update(results=flat_results)
    assert MethodWorkspace(method, draft(method)).last_results() is None, "as an earlier core wrote them"
    assert client.get(designer_url(method, "method_page")).status_code == 200
    MethodDraftRecord.objects.filter(pk=tests.pk).update(results=tests.results)
    results = client.get(designer_url(method, "method_results")).text
    assert "Results are up to date" in results
    post_to_designer(client, method, "method_step", operation="open", target="/with/steps/0")
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
    other = {"sheet": {"answers": {"Q1": "other"}}, "matches_expected": None, "results": {}, "max_marks": 1}
    assert post_callback(rerun, {"event": "finished", "outcome": [other]}) == 204
    tests = draft(method)
    assert run_state(tests)[0] == RunState("failed", tests.failure)
    assert "don't match the test answers" in tests.failure


FIRST_POINTS_PATH, SECOND_POINTS_PATH = "/with/steps/0/with/points", "/with/steps/1/with/points"


def test_a_field_is_filled_from_a_setting_or_fixed(client: Client, method: Method) -> None:
    two_checks = dump_yaml(
        {
            "name": "Light",
            "summary": "Says light.",
            "question_types": ["text"],
            "steps": [
                {"uses": "gradeflow/key-points@1", "with": {"points": ["light"]}},
                {"uses": "gradeflow/key-points@1", "with": {"points": ["sun"]}},
            ],
        }
    )
    post_to_designer(client, method, "method_yaml", definition=two_checks)
    post_to_designer(client, method, "method_field", operation="bind_new_setting", field=FIRST_POINTS_PATH)
    name = setting_name_at(core_draft(method), FIRST_POINTS_PATH)
    assert name in core_draft(method).declared_settings
    post_to_designer(client, method, "method_field", operation="bind", field=SECOND_POINTS_PATH, setting=name)
    assert setting_name_at(core_draft(method), SECOND_POINTS_PATH) == name
    post_to_designer(client, method, "method_field", operation="unbind", field=FIRST_POINTS_PATH)
    post_to_designer(client, method, "method_field", operation="unbind", field=SECOND_POINTS_PATH)
    method_draft = core_draft(method)
    assert (
        setting_name_at(method_draft, FIRST_POINTS_PATH),
        setting_name_at(method_draft, SECOND_POINTS_PATH),
        method_draft.declared_settings,
    ) == (
        None,
        None,
        {},
    )


def menu_items(page: str) -> list[tuple[str, bool]]:
    """Each source menu item's label, and whether it is selected."""
    menu_item_matches = re.findall(r'role="menuitemradio" aria-checked="(true|false)"[^>]*>\s*([^<]+?)\s*<', page)
    return [(label, checked == "true") for checked, label in menu_item_matches]


def test_a_fields_source_menu_marks_what_fills_it(client: Client, method: Method) -> None:
    first_check_query = {"path": "/with/steps/0"}
    page = client.get(designer_url(method, "method_page"), first_check_query).text
    assert menu_items(page)[0] == ("Fixed value", True)
    post_to_designer(client, method, "method_field", operation="bind_new_setting", field=FIRST_POINTS_PATH)
    page_with_setting = client.get(designer_url(method, "method_page"), first_check_query).text
    [declaration] = core_draft(method).declared_settings.values()
    assert menu_items(page_with_setting)[:2] == [("Fixed value", False), (declaration["title"], True)]
    assert page_with_setting.count("New setting") == page.count("New setting") - 1


def test_checks_of_a_method_share_a_setting(client: Client, method: Method) -> None:
    name = bind_new_setting(client, method, FIRST_POINTS_PATH)
    post_to_designer(
        client,
        method,
        "method_step",
        path="",
        operation="add",
        target="/with/steps/1",
        method="gradeflow/key-points@1",
    )
    post_to_designer(client, method, "method_field", operation="bind", field=SECOND_POINTS_PATH, setting=name)
    assert setting_name_at(core_draft(method), SECOND_POINTS_PATH) == name
    setting_page = declared_setting_page(client, method, name)
    label = BUILTINS.find("gradeflow/key-points@1").label
    assert f"{label} 1 · Points" in setting_page
    assert f"{label} 2 · Points" in setting_page
    second_step_page = client.get(designer_url(method, "method_page"), {"path": "/with/steps/1"}).text
    assert "New setting" in second_step_page, "a field sharing a setting can have its own"
    post_to_designer(client, method, "method_field", operation="bind_new_setting", field=SECOND_POINTS_PATH)
    method_draft = core_draft(method)
    second_setting = setting_name_at(method_draft, SECOND_POINTS_PATH)
    assert second_setting not in (None, name), "a setting of its own"
    assert set(method_draft.declared_settings) == {name, second_setting}


def test_the_label_help_and_default_of_a_setting_are_saved(client: Client, method: Method) -> None:
    post_to_designer(client, method, "method_field", operation="bind_new_setting", field=FIRST_POINTS_PATH)
    name = setting_name_at(core_draft(method), FIRST_POINTS_PATH)
    assert name is not None
    tab = client.get(designer_url(method, "method_settings"), {"setting": name, "tab": "settings"})
    assert 'name="setting-default"' in tab.text
    details = {"setting": name, "setting-title": "Words", "setting-description": "What answers must say."}
    post_to_designer(client, method, "method_settings", None, **details | {"setting-default": ["sun"]})
    assert core_draft(method).declared_settings[name] == {
        "title": "Words",
        "description": "What answers must say.",
        "default": ["sun"],
    }
    post_to_designer(client, method, "method_values", None, **{f"values-{name}": ["moon"]})
    assert saved_tests(method)["test_values"] == {name: ["moon"]}
    post_to_designer(client, method, "method_values", operation="reset")
    assert saved_tests(method)["test_values"] == {}


def test_a_checks_problems_show_while_a_setting_has_no_value_or_only_a_test_value(
    client: Client, method: Method
) -> None:
    key_points = {"uses": "gradeflow/key-points@1", "with": {"points": "{{ settings.points }}"}}
    expression = {"uses": "gradeflow/equivalent-expression@1", "with": {"answers": ["x2"]}}

    def page_with(*steps: dict[str, Any]) -> Any:
        definition = {
            "label": "Light",
            "summary": "Says light.",
            "question_types": ["text"],
            "settings": {"points": {"title": "Points"}},
            "steps": list(steps),
        }
        post_to_designer(client, method, "method_yaml", definition=dump_yaml(definition))
        return client.get(designer_url(method, "method_page"))

    assert page_with(key_points).context["problems"] == ["Give “Points” a test value or a default."]
    page = page_with(key_points, expression)
    assert (page.context["first_problem_step"], page.context["problems"]) == ("/with/steps/1", [])
    post_to_designer(client, method, "method_values", None, **{"values-points": ["sun"]})
    page = client.get(designer_url(method, "method_page"))
    assert (page.context["first_problem_step"], page.context["problems"]) == ("/with/steps/1", [])


def test_a_test_value_the_method_refuses_shows_at_its_field(client: Client, method: Method) -> None:
    definition = {
        "label": "Light",
        "summary": "Says light.",
        "question_types": ["text"],
        "settings": {"prefix": {"title": "Prefix pattern", "default": "^light"}},
        "steps": [{"uses": "gradeflow/pattern@1", "with": {"pattern": "{{ settings.prefix }}"}}],
    }
    post_to_designer(client, method, "method_yaml", definition=dump_yaml(definition))
    post_to_designer(client, method, "method_values", None, **{"values-prefix": "^(light"})
    page = client.get(designer_url(method, "method_page"))
    message = "Not a valid pattern: missing ) at position 7."
    assert page.context["problems"] == [f"Prefix pattern: {message}"]
    assert page.context["values"].errors["prefix"] == [message]


def test_every_part_of_a_setting_declaration_is_edited(client: Client, method: Method) -> None:
    marks_by_path = "/with/steps/0/with/marks_by"
    post_to_designer(client, method, "method_field", operation="bind_new_setting", field=marks_by_path)
    name = setting_name_at(core_draft(method), marks_by_path)
    assert name is not None
    tab = client.get(designer_url(method, "method_settings"), {"setting": name, "tab": "settings"}).text
    assert 'name="setting-advanced"' in tab
    assert 'name="setting-choices"' in tab
    fields = {
        "setting": name,
        "setting-title": "How points count",
        "setting-default": "any_correct",
        "setting-advanced": "True",
    }
    post_to_designer(
        client, method, "method_settings", None, **fields | {"setting-choices": ["all_correct", "any_correct"]}
    )
    assert core_draft(method).declared_settings[name] == {
        "title": "How points count",
        "default": "any_correct",
        "advanced": True,
        "choices": ["all_correct", "any_correct"],
    }
    cleared_fields: dict[str, object] = {
        "setting": name,
        "setting-title": "How points count",
        "setting-default": "",
        "setting-advanced": "",
    }
    post_to_designer(client, method, "method_settings", None, **cleared_fields)
    assert core_draft(method).declared_settings[name] == {"title": "How points count"}


MATCH_PATH, WHOLE_WORDS_PATH = (f"/with/steps/0/with/{key}" for key in ("match", "whole_words"))
MIN_LENGTH_PATH = "/with/steps/1/with/min_length"


@pytest.fixture
def method_with_length_check(client: Client, method: Method) -> Method:
    """`method` with a second check, on the answer's length."""
    checks = [{"uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}, {"uses": "gradeflow/length@1"}]
    text = dump_yaml({"label": "Light", "summary": "Says light.", "question_types": ["text"], "steps": checks})
    assert post_to_designer(client, method, "method_yaml", definition=text).status_code == 200
    return method


def bind_new_setting(client: Client, method: Method, field: str) -> str:
    post_to_designer(client, method, "method_field", operation="bind_new_setting", field=field)
    name = setting_name_at(core_draft(method), field)
    assert name is not None
    return name


def declared_setting_page(client: Client, method: Method, name: str) -> str:
    return client.get(designer_url(method, "method_settings"), {"setting": name, "tab": "settings"}).text


def setting_declaration_form(page: str) -> str:
    """The form of a page showing one setting."""
    form_match = re.search(r'<form class="grid gap-4[^"]*" hx-post="[^"]*/settings/">(.*?)</form>', page, re.DOTALL)
    assert form_match
    return form_match.group(1)


def options_of(page: str, name: str) -> list[tuple[str, str]]:
    """The options of the setting form's `name` control, by value and label."""
    if select_match := re.search(rf'<select name="setting-{name}".*?</select>', page, re.DOTALL):
        return re.findall(r'<option value="([^"]*)"[^>]*>\s*([^<]+?)\s*<', select_match.group(0))
    return re.findall(rf'name="setting-{name}" value="([^"]*)"[^>]*>\s*(?:<span[^>]*>)?\s*([^<]+?)\s*<', page)


def field_error(page: str, name: str) -> str:
    error_match = re.search(rf'id="id_setting-{name}_error">(.*?)</div>', page, re.DOTALL)
    return unescape(re.sub(r"<[^>]+>", "", error_match.group(1))).strip() if error_match else ""


def test_a_table_default_of_a_setting_comes_before_advanced(client: Client, method: Method) -> None:
    check = {"uses": "gradeflow/custom-code@1", "with": {"code": "correct = True\n"}}
    text = dump_yaml({"label": "Light", "summary": "Code.", "question_types": ["text"], "steps": [check]})
    post_to_designer(client, method, "method_yaml", definition=text)
    page = declared_setting_page(client, method, bind_new_setting(client, method, "/with/steps/0/with/parameters"))
    assert page.index('name="default-posted"') < page.index('name="setting-advanced"')


def test_the_choices_of_a_setting_are_the_options_of_the_field_it_fills(client: Client, method: Method) -> None:
    name = bind_new_setting(client, method, MATCH_PATH)
    page = declared_setting_page(client, method, name)
    options = [("exact", "Exactly"), ("spelling", "Similar spelling"), ("meaning", "Same meaning")]
    assert options_of(page, "choices") == options
    assert 'name="setting-examples"' not in page, "a setting with fixed options has nothing to suggest"
    fields: dict[str, object] = {
        "setting": name,
        "setting-title": "Match",
        "setting-choices": ["exact", "spelling"],
        "setting-default": "spelling",
    }
    post_to_designer(client, method, "method_settings", None, **fields)
    assert core_draft(method).declared_settings[name] == {
        "title": "Match",
        "default": "spelling",
        "choices": ["exact", "spelling"],
    }
    page = declared_setting_page(client, method, name)
    assert [value for value, _ in options_of(page, "default")] == ["", "exact", "spelling"]
    listing = client.get(designer_url(method, "method_settings"), {"tab": "settings"}).text
    assert "Default: Similar spelling" in listing


def test_choices_and_examples_show_only_where_they_apply(client: Client, method_with_length_check: Method) -> None:
    for field in (FIRST_POINTS_PATH, WHOLE_WORDS_PATH):
        page = declared_setting_page(
            client, method_with_length_check, bind_new_setting(client, method_with_length_check, field)
        )
        assert 'name="setting-choices"' not in page
        assert 'name="setting-examples"' not in page
    name = bind_new_setting(client, method_with_length_check, MIN_LENGTH_PATH)
    assert 'name="setting-choices"' not in declared_setting_page(client, method_with_length_check, name)
    post_to_designer(
        client, method_with_length_check, "method_settings", None, **{"setting": name, "setting-examples": ["2", "5"]}
    )
    assert core_draft(method_with_length_check).declared_settings[name]["examples"] == [2, 5]


def test_a_default_outside_the_choices_shows_on_the_default_with_what_was_typed(client: Client, method: Method) -> None:
    name = bind_new_setting(client, method, MATCH_PATH)
    before = core_draft(method).declared_settings[name]
    fields: dict[str, object] = {
        "setting": name,
        "setting-title": "How to match",
        "setting-choices": ["exact"],
        "setting-default": "spelling",
    }
    page = post_to_designer(client, method, "method_settings", None, **fields).text
    assert core_draft(method).declared_settings[name] == before
    assert field_error(page, "default") == "Similar spelling isn't one of the choices."
    assert 'value="How to match"' in page
    assert re.search(r'name="setting-choices" value="exact"[^>]*checked', page)
    assert not re.search(r'name="setting-choices" value="spelling"[^>]*checked', page)


def test_advanced_is_on_or_off_and_leaves_the_default_in_view(client: Client, method: Method) -> None:
    name = bind_new_setting(client, method, FIRST_POINTS_PATH)
    page = declared_setting_page(client, method, name)
    assert re.search(r'type="checkbox" name="setting-advanced"(?![^>]*checked)', page)
    fields: dict[str, object] = {"setting": name, "setting-title": "Points", "setting-default": ["sun"]}
    post_to_designer(client, method, "method_settings", None, **fields | {"setting-advanced": "on"})
    assert core_draft(method).declared_settings[name]["advanced"] is True
    page = declared_setting_page(client, method, name)
    assert "More options" not in page, "the setting's own default is never folded away"
    post_to_designer(client, method, "method_settings", None, **fields)
    assert "advanced" not in core_draft(method).declared_settings[name], "off, as the field it fills is"
    whole_words_setting = bind_new_setting(client, method, WHOLE_WORDS_PATH)
    page = declared_setting_page(client, method, whole_words_setting)
    assert re.search(r'type="checkbox" name="setting-advanced"[^>]*checked', page), "as the field it fills is"
    assert "More options" not in page


def test_a_yes_or_no_setting_may_have_no_default(client: Client, method: Method) -> None:
    name = bind_new_setting(client, method, WHOLE_WORDS_PATH)
    for default, saved in (("True", {"default": True}), ("", {})):
        post_to_designer(
            client,
            method,
            "method_settings",
            None,
            **{"setting": name, "setting-default": default, "setting-advanced": "on"},
        )
        assert core_draft(method).declared_settings[name] == {"title": "Whole words only", **saved}


def test_editing_a_check_keeps_the_fields_filled_by_settings(client: Client, method: Method) -> None:
    points_setting, match_setting, whole_words_setting = (
        bind_new_setting(client, method, field) for field in (FIRST_POINTS_PATH, MATCH_PATH, WHOLE_WORDS_PATH)
    )
    fields: dict[str, object] = {
        "setting": match_setting,
        "setting-choices": ["exact", "spelling"],
        "setting-default": "spelling",
    }
    post_to_designer(client, method, "method_settings", None, **fields)
    post_to_designer(
        client, method, "method_settings", None, **{"setting": whole_words_setting, "setting-default": "True"}
    )
    before = core_draft(method).declared_settings
    page = client.get(designer_url(method, "method_page"), {"path": "/with/steps/0"}).text
    for key in ("points", "match", "whole_words"):
        assert f'id="field-filled-by-setting-/with/steps/0/with/{key}"' in page
        assert f'name="{key}"' not in page
    assert '<span class="min-w-0 flex-1 truncate text-muted">Similar spelling</span>' in page
    post_to_designer(
        client, method, "method_step", path="/with/steps/0", fields="1", strictness="lenient", match="meaning"
    )
    assert core_draft(method).declared_settings == before
    assert before[points_setting]["default"] == ["light"]


def test_examples_are_typed_like_the_setting(client: Client, method_with_length_check: Method) -> None:
    name = bind_new_setting(client, method_with_length_check, MIN_LENGTH_PATH)
    page = post_to_designer(
        client, method_with_length_check, "method_settings", None, **{"setting": name, "setting-examples": ["2", "abc"]}
    )
    assert "examples" not in core_draft(method_with_length_check).declared_settings[name]
    text = page.text
    assert field_error(text, "examples") == "Must be a whole number."
    assert '<option value="abc" selected>' in text


def test_the_default_offers_the_examples_of_the_field_not_of_the_setting(
    client: Client, method_with_length_check: Method
) -> None:
    name = bind_new_setting(client, method_with_length_check, MIN_LENGTH_PATH)
    post_to_designer(
        client, method_with_length_check, "method_settings", None, **{"setting": name, "setting-examples": ["7"]}
    )
    assert 'data-preset="7"' not in setting_declaration_form(
        declared_setting_page(client, method_with_length_check, name)
    )
    assert 'data-preset="7"' in client.get(designer_url(method_with_length_check, "method_page")).text, (
        "where it's filled in"
    )


def test_a_whole_number_default_is_saved_whole(client: Client, method_with_length_check: Method) -> None:
    name = bind_new_setting(client, method_with_length_check, MIN_LENGTH_PATH)
    post_to_designer(
        client, method_with_length_check, "method_settings", None, **{"setting": name, "setting-default": "2"}
    )
    assert repr(core_draft(method_with_length_check).declared_settings[name]["default"]) == "2"


def test_the_default_explains_the_field_beside_the_description_of_the_setting(client: Client, method: Method) -> None:
    name = bind_new_setting(client, method, WHOLE_WORDS_PATH)
    post_to_designer(
        client, method, "method_settings", None, **{"setting": name, "setting-description": "How close it must be."}
    )
    page = setting_declaration_form(declared_setting_page(client, method, name))
    assert page.count("How close it must be.") == 1
    method_draft = core_draft(method)
    field = next(field for field in method_draft.configurable_fields() if field.path == WHOLE_WORDS_PATH)
    setting = method_draft.schema(field.step_path)["properties"][field.key]
    assert html.unescape(setting["description"]) in html.unescape(page)


def test_a_check_named_by_a_setting_shows_the_name_it_gets(client: Client, method: Method) -> None:
    name = bind_new_setting(client, method, "/with/steps/0/name")

    def page_at(path: str) -> str:
        page = client.get(designer_url(method, "method_page"), {"path": path}).text
        assert "{{ settings." not in page
        return page

    label = BUILTINS.find("gradeflow/key-points@1").label
    assert f">{label}</h3>" in page_at("/with/steps/0"), "without a default, as its method"
    post_to_designer(client, method, "method_settings", None, **{"setting": name, "setting-default": "Says light"})
    assert ">Says light</button>" in page_at("")
    assert ">Says light</h3>" in page_at("/with/steps/0")
    assert "Says light · Name" in declared_setting_page(client, method, name)


def test_rows_of_a_setting_are_added_and_removed_before_saving(client: Client, method: Method) -> None:
    check = {"uses": "gradeflow/code-tests@1", "with": {"tests": [{"expression": "f(1)", "expected": "2"}]}}
    code_tests_yaml = dump_yaml(
        {"name": "Light", "summary": "Says light.", "question_types": ["text"], "steps": [check]}
    )
    post_to_designer(client, method, "method_yaml", definition=code_tests_yaml)
    tests = "/with/steps/0/with/tests"
    post_to_designer(client, method, "method_field", operation="bind_new_setting", field=tests)
    name = setting_name_at(core_draft(method), tests)
    assert name is not None
    row: dict[str, object] = {
        "setting": name,
        "default-posted": "1",
        "default-0-expression": "f(1)",
        "default-0-expected": "2",
    }
    page = post_to_designer(
        client, method, "method_settings", None, **row | {"operation": "add_row", "target": "default"}
    )
    assert 'name="default-1-expression"' in page.text
    page = post_to_designer(
        client, method, "method_settings", None, **row | {"operation": "remove_row", "target": "default", "value": "0"}
    )
    assert 'name="default-0-expression"' not in page.text
    assert core_draft(method).declared_settings[name]["default"] == [{"expression": "f(1)", "expected": "2"}], (
        "saved only on Save"
    )


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
    method_draft = core_draft(method)
    assert method_draft.question is None
    assert {method_draft.data["with"]["steps"][0].get("question")} == {"Q1"}
    assert method_draft.problems == []


def set_question_in_tests(method: Method, question: dict[str, object]) -> None:
    """Make Q1 of the method's tests `question`."""
    draft_record = draft(method)
    draft_record.tests["questions"]["Q1"] = question
    draft_record.save()


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
    response = post_to_designer(client, method, "method_yaml", definition="steps: [")
    assert response.status_code == 422
    assert 'role="alert"' in response.text
    assert draft(method).definition == before
    assert (
        post_to_designer(client, method, "method_yaml", definition=method_definition(answer="sun")).status_code == 200
    )
    assert "sun" in draft(method).definition


def test_an_edit_from_an_older_page_changes_nothing(client: Client, method: Method) -> None:
    draft_record = designer_record(method, None)
    response = client.post(
        designer_url(method, "method_answers"),
        {"revision": draft_record.revision - 1, "operation": "add", "answer": "x"},
        headers=HTMX_HEADERS,
    )
    assert response.status_code == 409
    assert "This method changed in another tab." in response.text
    publish_response = client.post(designer_url(method, "method_publish"), {"revision": draft_record.revision - 1})
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
    assert post_to_designer(client, method, "method_yaml", 1, definition="steps: [").status_code == 200
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
    assert values.status_code == 404, "a version's test values are those it was published with"
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
    remove_response = post_to_designer(client, method, "method_settings", 1, setting="points", operation="remove")
    assert remove_response.status_code == 404
    assert MethodDraftRecord.objects.get(method=method).definition == definition
    assert enabled_fields(version_page) == []
    assert 'class="flex flex-1 flex-col read-only-fields"' in page
    draft_page = client.get(designer_url(method, "method_page"), {"path": "/with/steps/0"})
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
        definition_data(method.versions.get(number=3).definition)["steps"]
        == definition_data(method.versions.get(number=1).definition)["steps"]
    )


def test_editing_needs_a_published_version(client: Client, method: Method) -> None:
    assert client.post(reverse("method_edit", args=[method.pk])).status_code == 404


def test_a_row_that_isnt_a_number_is_a_missing_row(client: Client, method: Method) -> None:
    response = post_to_designer(client, method, "method_answers", operation="delete", row="first")
    assert response.status_code == 200
    assert "This answer is no longer there." in response.text


def test_a_checks_method_is_chosen_from_the_picker(client: Client, method: Method) -> None:
    first_check_query = {"path": "/with/steps/0"}
    assert (
        'operation=change_method&amp;target=/with/steps/0"'
        in client.get(designer_url(method, "method_page"), first_check_query).text
    )
    picker = client.get(
        designer_url(method, "method_choices"), {"operation": "change_method", "target": "/with/steps/0"}
    )
    accepted = BUILTINS.find("gradeflow/accepted-answers@1")
    assert escape(accepted.label) in picker.text
    post_to_designer(
        client,
        method,
        "method_step",
        path="/with/steps/0",
        operation="change_method",
        target="/with/steps/0",
        method="gradeflow/accepted-answers@1",
    )
    assert definition_data(draft(method).definition)["steps"][0]["uses"] == "gradeflow/accepted-answers@1"


def test_a_method_gives_marks_or_a_steps_output(client: Client, method: Method) -> None:
    post_to_designer(
        client,
        method,
        "method_step",
        path="",
        operation="add",
        target="/with/steps/1",
        method="gradeflow/extract@1",
    )
    output_step = "{{ steps.extract.output }}"

    def method_top_level_page() -> Response:
        return client.get(designer_url(method, "method_page"))

    def marks_by_and_output_step_hidden() -> tuple[bool, bool]:
        form = method_top_level_page().context["editor"].form
        return form["marks_by"].starts_hidden, form["output_step"].starts_hidden

    def post_top_level_fields(**fields: str) -> None:
        post_to_designer(client, method, "method_step", path="", fields_posted="1", **fields)

    page = method_top_level_page()
    assert 'operation=change_method&amp;target="' not in page.text
    assert list(page.context["editor"].form["output"].field.choices) == [
        ("marks", "Marks"),
        ("step_output", "A step's output"),
    ]
    assert output_step in dict(page.context["editor"].form["output_step"].field.choices)
    assert marks_by_and_output_step_hidden() == (False, True)

    post_top_level_fields(output="step_output", output_step=output_step)
    saved_definition = definition_data(draft(method).definition)
    assert (saved_definition["output"], saved_definition["output_step"]) == ("step_output", output_step)
    assert marks_by_and_output_step_hidden() == (True, False), "as reloaded"
    steps_settings = core_draft(method).data["with"]
    assert (steps_settings["output"], steps_settings["output_step"]) == ("step_output", output_step)

    post_top_level_fields(output="marks", marks_by="total")
    saved_definition = definition_data(draft(method).definition)
    assert (saved_definition.get("output", "marks"), saved_definition["marks_by"]) == ("marks", "total")
    assert marks_by_and_output_step_hidden() == (False, True)


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
            designer_url(method, "method_choices"), {"operation": "change_method", "target": "/with/steps/5"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("steps_definition", "read_only"),
    [
        (
            {
                "steps": [
                    {"question": "Q1", "uses": "gradeflow/key-points@1", "with": {"points": ["a"]}},
                    {"question": "Q2", "weight": 0, "uses": "gradeflow/key-points@1", "with": {"points": ["a"]}},
                ],
            },
            {"Q2"},
        ),
        (
            {
                "output": "step_output",
                "output_step": "{{ steps.number.output }}",
                "steps": [{"id": "number", "question": "Q1", "uses": "gradeflow/extract@1"}],
            },
            set(),
        ),
    ],
)
def test_the_page_says_which_questions_a_group_method_only_reads(
    client: Client, method: Method, steps_definition: dict[str, object], read_only: set[str]
) -> None:
    definition = {"label": "Group", "summary": "Reads two.", "marks_question_group": True, **steps_definition}
    record = draft(method)
    record.tests["questions"] = {"Q1": {"type": "text"}, "Q2": {"type": "text"}}
    record.definition = dump_yaml(definition)
    record.save()
    page = client.get(designer_url(method, "method_page"))
    assert page.status_code == 200
    assert page.context["read_only"] == read_only
