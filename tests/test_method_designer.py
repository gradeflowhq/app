"""The method designer: a person's draft of a method, or a published version they try, beside their test answers."""

import html
import re
from datetime import timedelta
from html import unescape
from html.parser import HTMLParser
from typing import TYPE_CHECKING

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape
from gradeflow_core import BUILTINS, Problem, Question
from gradeflow_core.editing import MethodDraft as CoreDraft
from gradeflow_core.editing import dump_yaml
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.models import Job
from gradeflow_app.libraries.designer import presenters as designer_presenters
from gradeflow_app.libraries.designer import views as designer_views
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.models import Method, MethodDraft, VersionTests
from gradeflow_app.libraries.workbench import Tests as Answers
from gradeflow_app.marking.checks import tree_of
from gradeflow_app.questions.models import TYPE_LABELS
from tests.conftest import (
    HTMX,
    Idle,
    call_back,
    course_of,
    definition,
    edit,
    posted,
    publish,
    session,
    signed_in,
    url,
)

if TYPE_CHECKING:
    pass

pytestmark = pytest.mark.django_db


@pytest.fixture
def course(user: User) -> Course:
    return course_of(user, "Method laboratory")


@pytest.fixture
def method(client: Client, course: Course) -> Method:
    """A new method in the course's library, `lab`, its draft marking text answers with the keyword `light`."""
    response = client.post(new_url(course), new_method("Light"))
    assert response.status_code == 302
    created = Method.objects.get(library__course=course)
    assert edit(client, created, "method_yaml", definition=definition()).status_code == 200
    return created


def new_url(course: Course) -> str:
    return f"{reverse('method_new')}?course={course.pk}"


def new_method(name: str) -> dict[str, str]:
    return {"label": name, "summary": "Says light.", "question_type": "text"}


def draft(method: Method) -> MethodDraft:
    return MethodDraft.objects.get(method=method)


def saved_tests(method: Method, number: int | None = None) -> Answers:
    tests: Answers = session(method, number).tests
    return tests


def core_of(method: Method) -> CoreDraft:
    """The method's draft as the core reads it."""
    return MethodWorkspace(method, draft(method)).core


def input_of(core: CoreDraft, path: str) -> str | None:
    return next(field.input for field in core.fields() if field.path == path)


def answers(method: Method, number: int | None = None) -> list[tuple[str, bool]]:
    return [(row["answers"]["Q1"], row["example"]) for row in saved_tests(method, number).get("rows", [])]


CONTROLS = ("input", "select", "textarea")


class MarkingControls(HTMLParser):
    """The names of the controls of a page's marking form that can be changed."""

    def __init__(self, page: str) -> None:
        super().__init__()
        self.inside, self.fieldsets, self.enabled = False, [False], list[str]()
        self.feed(page)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        found = dict(attrs)
        if tag == "form":
            self.inside = found.get("id") == "marking-form"
        elif tag == "fieldset":
            self.fieldsets.append(self.fieldsets[-1] or "disabled" in found)
        elif self.inside and tag in CONTROLS and found.get("type") != "hidden" and not self.disabled(found):
            self.enabled.append(found.get("name") or tag)

    def disabled(self, attrs: dict[str, str | None]) -> bool:
        return "disabled" in attrs or self.fieldsets[-1]

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.inside = False
        elif tag == "fieldset":
            self.fieldsets.pop()


def test_a_new_method_starts_as_a_draft(client: Client, course: Course, method: Method) -> None:
    listing = client.get(reverse("course_methods", args=[course.pk])).content.decode()
    assert f'hx-post="{escape(new_url(course))}"' in listing, "New method is a dialog on the Methods page"
    assert "Save to" not in listing
    assert method.library == course.library
    page = client.get(url(method, "method_page")).content.decode()
    assert "Light" in page
    assert "Draft" in page
    assert "read-only-fields" not in page
    assert ">Methods</a></span>" not in page
    assert ">Publish</button>" in page
    assert not method.versions.exists()
    listing = client.get(reverse("course_methods", args=[course.pk])).content.decode()
    assert "Says light." in listing, "a draft says what it does too"
    assert reverse("published", args=[method.pk]).endswith("/")
    assert client.get(reverse("published", args=[method.pk])).headers["Location"] == url(method, "method_page")


def test_test_answers_are_added_changed_and_removed_whole(client: Client, method: Method) -> None:
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_answers", op="add", answer="dark")
    assert answers(method) == [("light", False), ("dark", False)]
    page = client.get(url(method, "method_page")).content.decode()
    assert re.search(r'aria-label="Answer of submission 2"[^>]*>dark</textarea>', page)
    edit(client, method, "method_answers", op="save", row="1", answer="sunlight")
    edit(client, method, "method_answers", op="example", row="0")
    assert answers(method) == [("light", True), ("sunlight", False)]
    edit(client, method, "method_answers", op="clear", row="1")
    assert answers(method) == [("light", True), ("", False)]
    edit(client, method, "method_answers", op="delete", row="0")
    assert answers(method) == [("", False)]
    response = edit(client, method, "method_answers", op="delete", row="5")
    assert "This answer is no longer there." in response.content.decode()


def test_chosen_answers_become_the_methods_example(client: Client, method: Method) -> None:
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_answers", op="example", row="0")
    [example] = core_of(method).examples
    assert [row.answers for row in example.rows] == [{"Q1": "light"}]
    assert saved_tests(method)["example_at"] == 0


def test_running_the_tests_shows_the_marks_until_the_answers_change(
    client: Client, method: Method, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_answers", op="add", answer="dark")
    with django_capture_on_commit_callbacks(execute=True):
        edit(client, method, "method_run")
    page = client.get(url(method, "method_page")).content.decode()
    job = Job.objects.get(kind=Job.Kind.DRAFT_TESTS)
    assert job.status == Job.Status.COMPLETED, job.error
    assert [row["answers"] for row in job.result or []] == [{"Q1": "light"}, {"Q1": "dark"}], "a result per answer"
    assert "Results are up to date" in page
    results = client.get(url(method, "method_results")).content.decode()
    assert "Results are up to date" in results
    edit(client, method, "method_check", op="open", target="with.checks.0")
    assert "Results are up to date" in client.get(url(method, "method_results")).content.decode(), "only looked"
    edit(client, method, "method_answers", op="add", answer="lights")
    assert "Changed since the last run" in client.get(url(method, "method_results")).content.decode()


def test_unreadable_test_results_fail_the_run(client: Client, method: Method, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_run")
    job = Job.objects.get(kind=Job.Kind.DRAFT_TESTS)
    assert call_back(job, {"event": "tested", "results": [{"answers": {"Q1": "light"}}]}) == 204
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, events.UNREADABLE)
    edit(client, method, "method_run")
    rerun = Job.objects.filter(kind=Job.Kind.DRAFT_TESTS).exclude(pk=job.pk).get()
    other = {"answers": {"Q1": "other"}, "matches": None, "results": {}}
    assert call_back(rerun, {"event": "tested", "results": [other]}) == 204
    assert call_back(rerun, {"event": "finished"}) == 204
    rerun.refresh_from_db()
    assert rerun.status == Job.Status.FAILED
    assert "don't match the test answers" in rerun.error


FIRST, SECOND = "with.checks.0.with.points", "with.checks.1.with.points"


def test_a_setting_is_filled_from_an_input_or_fixed(client: Client, method: Method) -> None:
    two = dump_yaml(
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
    edit(client, method, "method_yaml", definition=two)
    edit(client, method, "method_field", op="new", field=FIRST)
    name = input_of(core_of(method), FIRST)
    assert name in core_of(method).inputs
    edit(client, method, "method_field", op="bind", field=SECOND, input=name)
    assert input_of(core_of(method), SECOND) == name
    edit(client, method, "method_field", op="fix", field=FIRST)
    edit(client, method, "method_field", op="fix", field=SECOND)
    core = core_of(method)
    assert (input_of(core, FIRST), input_of(core, SECOND), core.inputs) == (None, None, {})


def menu_items(page: str) -> list[tuple[str, bool]]:
    """The choices of the menus beside settings an input can fill, and whether each is what fills it."""
    found = re.findall(r'role="menuitemradio" aria-checked="(true|false)"[^>]*>\s*([^<]+?)\s*<', page)
    return [(label, checked == "true") for checked, label in found]


def test_a_settings_menu_marks_what_fills_it(client: Client, method: Method) -> None:
    points = {"path": "with.checks.0"}
    page = client.get(url(method, "method_page"), points).content.decode()
    assert menu_items(page)[0] == ("Fixed value", True)
    edit(client, method, "method_field", op="new", field=FIRST)
    bound = client.get(url(method, "method_page"), points).content.decode()
    [spec] = core_of(method).inputs.values()
    assert menu_items(bound)[:2] == [("Fixed value", False), (spec["title"], True)]
    assert bound.count("New input") == page.count("New input") - 1


def test_checks_of_a_method_share_an_input(client: Client, method: Method) -> None:
    name = new_input(client, method, FIRST)
    edit(client, method, "method_check", path="", op="add", target="with.checks.1", value="gradeflow/key-points@1")
    edit(client, method, "method_field", op="bind", field=SECOND, input=name)
    assert input_of(core_of(method), SECOND) == name
    fills = input_form(client, method, name)
    assert "Key Points 1 · Points" in fills
    assert "Key Points 2 · Points" in fills
    edit(client, method, "method_field", op="new", field=SECOND)
    core = core_of(method)
    own = input_of(core, SECOND)
    assert own not in (None, name), "an input of its own"
    assert set(core.inputs) == {name, own}


def test_an_inputs_label_help_and_default_are_saved(client: Client, method: Method) -> None:
    edit(client, method, "method_field", op="new", field=FIRST)
    name = input_of(core_of(method), FIRST)
    assert name is not None
    tab = client.get(url(method, "method_inputs"), {"input": name, "tab": "inputs"})
    assert 'name="input-default"' in tab.content.decode()
    details = {"input": name, "input-title": "Words", "input-description": "What answers must say."}
    posted(client, method, "method_inputs", None, details | {"input-default": ["sun"]})
    assert core_of(method).inputs[name] == {
        "title": "Words",
        "description": "What answers must say.",
        "default": ["sun"],
    }
    posted(client, method, "method_values", None, {f"values-{name}": ["moon"]})
    assert saved_tests(method)["test_values"] == {name: ["moon"]}
    edit(client, method, "method_values", op="reset")
    assert saved_tests(method)["test_values"] == {}


def test_every_part_of_an_inputs_declaration_is_edited(client: Client, method: Method) -> None:
    combine = "with.checks.0.with.combine"
    edit(client, method, "method_field", op="new", field=combine)
    name = input_of(core_of(method), combine)
    assert name is not None
    tab = client.get(url(method, "method_inputs"), {"input": name, "tab": "inputs"}).content.decode()
    assert 'name="input-advanced"' in tab
    assert 'name="input-choices"' in tab
    chosen = {"input": name, "input-title": "Combine", "input-default": "any", "input-advanced": "True"}
    posted(client, method, "method_inputs", None, chosen | {"input-choices": ["all", "any"]})
    assert core_of(method).inputs[name] == {
        "title": "Combine",
        "default": "any",
        "advanced": True,
        "choices": ["all", "any"],
    }
    cleared: dict[str, object] = {"input": name, "input-title": "Combine", "input-default": "", "input-advanced": ""}
    posted(client, method, "method_inputs", None, cleared)
    assert core_of(method).inputs[name] == {"title": "Combine"}


MATCH, WHOLE_WORDS = (f"with.checks.0.with.{key}" for key in ("match", "whole_words"))
AT_LEAST = "with.checks.1.with.min_length"


@pytest.fixture
def counted(client: Client, method: Method) -> Method:
    """`method` with a second check, on the answer's length."""
    checks = [{"uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}, {"uses": "gradeflow/length@1"}]
    text = dump_yaml({"label": "Light", "summary": "Says light.", "question_types": ["text"], "checks": checks})
    assert edit(client, method, "method_yaml", definition=text).status_code == 200
    return method


def new_input(client: Client, method: Method, field: str) -> str:
    edit(client, method, "method_field", op="new", field=field)
    name = input_of(core_of(method), field)
    assert name is not None
    return name


def input_form(client: Client, method: Method, name: str) -> str:
    return client.get(url(method, "method_inputs"), {"input": name, "tab": "inputs"}).content.decode()


def settings_of(page: str) -> str:
    """The input form of a page showing one input."""
    found = re.search(r'<form class="grid gap-4[^"]*" hx-post="[^"]*/inputs/">(.*?)</form>', page, re.DOTALL)
    assert found
    return found.group(1)


def offered(page: str, name: str) -> list[tuple[str, str]]:
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
    edit(client, method, "method_yaml", definition=text)
    page = input_form(client, method, new_input(client, method, "with.checks.0.with.parameters"))
    assert page.index('name="default-present"') < page.index('name="input-advanced"')


def test_an_inputs_choices_are_the_options_of_the_setting_it_fills(client: Client, method: Method) -> None:
    name = new_input(client, method, MATCH)
    page = input_form(client, method, name)
    options = [("exact", "Exactly"), ("spelling", "Similar spelling"), ("meaning", "Same meaning")]
    assert offered(page, "choices") == options
    assert 'name="input-examples"' not in page, "a setting with fixed options has nothing to suggest"
    chosen: dict[str, object] = {
        "input": name,
        "input-title": "Match",
        "input-choices": ["exact", "spelling"],
        "input-default": "spelling",
    }
    posted(client, method, "method_inputs", None, chosen)
    assert core_of(method).inputs[name] == {"title": "Match", "default": "spelling", "choices": ["exact", "spelling"]}
    page = input_form(client, method, name)
    assert [value for value, _ in offered(page, "default")] == ["", "exact", "spelling"]
    listing = client.get(url(method, "method_inputs"), {"tab": "inputs"}).content.decode()
    assert "Default: Similar spelling" in listing


def test_choices_and_examples_show_only_where_they_apply(client: Client, counted: Method) -> None:
    for field in (FIRST, WHOLE_WORDS):
        page = input_form(client, counted, new_input(client, counted, field))
        assert 'name="input-choices"' not in page
        assert 'name="input-examples"' not in page
    name = new_input(client, counted, AT_LEAST)
    assert 'name="input-choices"' not in input_form(client, counted, name)
    posted(client, counted, "method_inputs", None, {"input": name, "input-examples": ["2", "5"]})
    assert core_of(counted).inputs[name]["examples"] == [2, 5]


def test_a_default_outside_the_choices_shows_on_the_default_with_what_was_typed(client: Client, method: Method) -> None:
    name = new_input(client, method, MATCH)
    before = core_of(method).inputs[name]
    typed: dict[str, object] = {
        "input": name,
        "input-title": "How to match",
        "input-choices": ["exact"],
        "input-default": "spelling",
    }
    page = posted(client, method, "method_inputs", None, typed).content.decode()
    assert core_of(method).inputs[name] == before
    assert field_error(page, "default") == "Similar spelling isn't one of the choices."
    assert 'value="How to match"' in page
    assert re.search(r'name="input-choices" value="exact"[^>]*checked', page)
    assert not re.search(r'name="input-choices" value="spelling"[^>]*checked', page)


def test_advanced_is_on_or_off_and_leaves_the_default_in_view(client: Client, method: Method) -> None:
    name = new_input(client, method, FIRST)
    page = input_form(client, method, name)
    assert re.search(r'type="checkbox" name="input-advanced"(?![^>]*checked)', page)
    kept: dict[str, object] = {"input": name, "input-title": "Points", "input-default": ["sun"]}
    posted(client, method, "method_inputs", None, kept | {"input-advanced": "on"})
    assert core_of(method).inputs[name]["advanced"] is True
    page = input_form(client, method, name)
    assert "More options" not in page, "the input's own default is never folded away"
    posted(client, method, "method_inputs", None, kept)
    assert "advanced" not in core_of(method).inputs[name], "off, as the setting it fills is"
    hidden = new_input(client, method, WHOLE_WORDS)
    page = input_form(client, method, hidden)
    assert re.search(r'type="checkbox" name="input-advanced"[^>]*checked', page), "as the setting it fills is"
    assert "More options" not in page


def test_a_yes_or_no_input_may_have_no_default(client: Client, method: Method) -> None:
    name = new_input(client, method, WHOLE_WORDS)
    for default, saved in (("True", {"default": True}), ("", {})):
        posted(client, method, "method_inputs", None, {"input": name, "input-default": default, "input-advanced": "on"})
        assert core_of(method).inputs[name] == {"title": "Whole words only", **saved}


def test_an_edit_of_a_check_leaves_what_inputs_fill(client: Client, method: Method) -> None:
    points, match, whole = (new_input(client, method, field) for field in (FIRST, MATCH, WHOLE_WORDS))
    chosen: dict[str, object] = {"input": match, "input-choices": ["exact", "spelling"], "input-default": "spelling"}
    posted(client, method, "method_inputs", None, chosen)
    posted(client, method, "method_inputs", None, {"input": whole, "input-default": "True"})
    before = core_of(method).inputs
    page = client.get(url(method, "method_page"), {"path": "with.checks.0"}).content.decode()
    for key in ("points", "match", "whole_words"):
        assert f'id="filled-with.checks.0.with.{key}"' in page
        assert f'name="{key}"' not in page
    assert 'Default <span class="text-base-content">Similar spelling</span>' in page
    edit(client, method, "method_check", path="with.checks.0", fields="1", strictness="lenient", match="meaning")
    assert core_of(method).inputs == before
    assert before[points]["default"] == ["light"]


def test_examples_are_typed_like_the_setting(client: Client, counted: Method) -> None:
    name = new_input(client, counted, AT_LEAST)
    page = posted(client, counted, "method_inputs", None, {"input": name, "input-examples": ["2", "abc"]})
    assert "examples" not in core_of(counted).inputs[name]
    text = page.content.decode()
    assert field_error(text, "examples") == "Must be a whole number."
    assert '<option value="abc" selected>' in text


def test_the_default_offers_the_settings_examples_not_the_inputs(client: Client, counted: Method) -> None:
    name = new_input(client, counted, AT_LEAST)
    posted(client, counted, "method_inputs", None, {"input": name, "input-examples": ["7"]})
    assert 'data-preset="7"' not in settings_of(input_form(client, counted, name))
    assert 'data-preset="7"' in client.get(url(counted, "method_page")).content.decode(), "where it's filled in"


def test_a_whole_number_default_is_saved_whole(client: Client, counted: Method) -> None:
    name = new_input(client, counted, AT_LEAST)
    posted(client, counted, "method_inputs", None, {"input": name, "input-default": "2"})
    assert repr(core_of(counted).inputs[name]["default"]) == "2"


def test_the_default_explains_the_setting_beside_the_inputs_description(client: Client, method: Method) -> None:
    name = new_input(client, method, WHOLE_WORDS)
    posted(client, method, "method_inputs", None, {"input": name, "input-description": "How close it must be."})
    page = settings_of(input_form(client, method, name))
    assert page.count("How close it must be.") == 1
    core = core_of(method)
    field = next(field for field in core.fields() if field.path == WHOLE_WORDS)
    setting = core.checks.schema(field.check)["properties"][field.key]
    assert html.unescape(setting["description"]) in html.unescape(page)


def test_a_check_named_by_an_input_shows_the_name_it_gets(client: Client, method: Method) -> None:
    name = new_input(client, method, "with.checks.0.name")

    def shown(path: str) -> str:
        page = client.get(url(method, "method_page"), {"path": path}).content.decode()
        assert "{{ inputs." not in page
        return page

    assert ">Key Points</h3>" in shown("with.checks.0"), "without a default, as its method"
    posted(client, method, "method_inputs", None, {"input": name, "input-default": "Says light"})
    assert ">Says light</button>" in shown("")
    assert ">Says light</h3>" in shown("with.checks.0")
    assert "Says light · Name" in input_form(client, method, name)


def test_an_inputs_rows_are_added_and_removed_before_saving(client: Client, method: Method) -> None:
    check = {"uses": "gradeflow/code-tests@1", "with": {"tests": [{"expression": "f(1)", "expected": "2"}]}}
    code = dump_yaml({"name": "Light", "summary": "Says light.", "question_types": ["text"], "checks": [check]})
    edit(client, method, "method_yaml", definition=code)
    tests = "with.checks.0.with.tests"
    edit(client, method, "method_field", op="new", field=tests)
    name = input_of(core_of(method), tests)
    assert name is not None
    row: dict[str, object] = {
        "input": name,
        "default-present": "1",
        "default-0-expression": "f(1)",
        "default-0-expected": "2",
    }
    page = posted(client, method, "method_inputs", None, row | {"op": "add_row", "target": "default"})
    assert 'name="default-1-expression"' in page.content.decode()
    page = posted(client, method, "method_inputs", None, row | {"op": "remove_row", "target": "default", "value": "0"})
    assert 'name="default-0-expression"' not in page.content.decode()
    assert core_of(method).inputs[name]["default"] == [{"expression": "f(1)", "expected": "2"}], "saved only on Save"


def test_questions_are_added_and_removed_with_their_answers(client: Client, method: Method) -> None:
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_question", save="1", type="number", max_marks="2")
    tests = saved_tests(method)
    assert list(tests["questions"]) == ["Q1", "Q2"]
    assert tests["rows"] == [{"answers": {"Q1": "light", "Q2": ""}, "example": False}]
    edit(client, method, "method_question", key="Q2", op="remove")
    tests = saved_tests(method)
    assert list(tests["questions"]) == ["Q1"]
    assert tests["rows"] == [{"answers": {"Q1": "light"}, "example": False}]


def test_a_second_question_keeps_the_checks_on_the_first(client: Client, method: Method) -> None:
    edit(client, method, "method_question", save="1", type="number", max_marks="2")
    core = core_of(method)
    assert core.question is None
    assert {core.checks.data["with"]["checks"][0].get("question")} == {"Q1"}
    assert core.problems == []


def ask(method: Method, question: dict[str, object]) -> None:
    """Make Q1 of the method's tests `question`."""
    session = draft(method)
    session.tests["questions"]["Q1"] = question
    session.save()


def asked(method: Method) -> Question:
    """Q1 of the method's tests, as the core reads it."""
    return Question.load(saved_tests(method)["questions"]["Q1"])


def test_an_answer_of_several_values_is_written_value_by_value(client: Client, method: Method) -> None:
    ask(method, {"type": "values", "value_types": ["number", "number"], "reading": {"separator": "~"}})
    question = asked(method)
    schema = question.answer_schema()
    titles = {name: escape(prop["title"]) for name, prop in schema["properties"].items()}
    page = client.get(url(method, "method_page"), headers=HTMX).content.decode()
    assert all(f'placeholder="{title}"' in page for title in titles.values())
    typed = [dict(zip(titles, values, strict=True)) for values in (("2", " -3 "), ("", " "))]
    for values in typed:
        posted(client, method, "method_answers", None, {"op": "add", **{f"value-{k}": v for k, v in values.items()}})
    written = [question.answer_from(values) for values in typed]
    assert answers(method) == [(answer, False) for answer in written]
    page = client.get(url(method, "method_page"), headers=HTMX).content.decode()
    for name, value in question.answer_values(written[0]).items():
        assert f'title="{titles[name]}">{escape(value)}</span>' in page
        assert f'name="value-{name}" value="{escape(value)}"' in page


def test_a_choice_answer_is_ticked_from_the_options(client: Client, method: Method) -> None:
    ask(method, {"type": "choice", "options": ["A", "B", "C"], "allow_multiple": True})
    question = asked(method)
    posted(client, method, "method_answers", None, {"op": "add", "value-options": ["c", "a"]})
    edit(client, method, "method_answers", op="add")
    assert answers(method) == [(question.answer_from({"options": ["c", "a"]}), False), ("", False)]
    posted(client, method, "method_answers", None, {"op": "save", "row": "0", "value-options": ["b"]})
    assert answers(method)[0] == (question.answer_from({"options": ["b"]}), False)


def test_an_answer_of_several_options_shows_each_option(client: Client, method: Method) -> None:
    ask(method, {"type": "choice", "options": ["a", "b", "c"], "allow_multiple": True})
    posted(client, method, "method_answers", None, {"op": "add", "value-options": ["c", "a"]})
    page = client.get(url(method, "method_page"), headers=HTMX).content.decode()
    shown = re.findall(r'<span class="badge[^"]*" title="[^"]+">([^<]*)</span>', page)
    assert shown == ["a", "c"]


def test_a_text_answer_is_written_whole(client: Client, method: Method) -> None:
    page = client.get(url(method, "method_page"), headers=HTMX).content.decode()
    assert 'name="value-' not in page
    edit(client, method, "method_answers", op="add", answer="light\nand heat")
    assert answers(method) == [("light\nand heat", False)]


def test_a_draft_is_listed_as_it_says_it_is_so_far(client: Client, method: Method, course: Course) -> None:
    edit(client, method, "method_yaml", definition="label: Half done\nsummary: Not yet.\nchecks: []\n")
    listed = client.get(reverse("course_methods", args=[course.pk])).content.decode()
    text = TYPE_LABELS["text"]
    assert re.search(rf">Half done</a>.*Not yet\.</p></td>\s*<td[^>]*>{re.escape(text)}</td>", listed, re.DOTALL)


def test_details_are_saved(client: Client, method: Method) -> None:
    sheet = client.get(url(method, "method_details")).content.decode()
    assert 'name="summary"' in sheet
    assert "<markdown-toolbar" in sheet, "the guide is written as Markdown"
    edit(client, method, "method_details", label="Bright", summary="Says bright.", question_types=["text"])
    tree = tree_of(draft(method).definition)
    assert (tree["label"], tree["summary"]) == ("Bright", "Says bright.")


def test_details_saved_as_shown_leave_the_definition_as_it_was(client: Client, method: Method) -> None:
    edit(client, method, "method_yaml", definition="label: All\nsummary: Any.\nquestion_types: all\nchecks: []\n")
    before = draft(method).definition
    sheet = client.get(url(method, "method_details")).content.decode()
    shown = {name: value for name, value in core_of(method).metadata.items() if value}
    assert all(re.search(f'value="{kind}"[^>]* checked', sheet) for kind in shown["question_types"])
    edit(client, method, "method_details", **shown)
    assert draft(method).definition == before


def test_yaml_the_core_cant_read_leaves_the_draft_as_it_was(client: Client, method: Method) -> None:
    before = draft(method).definition
    response = edit(client, method, "method_yaml", definition="checks: [")
    assert response.status_code == 422
    assert 'role="alert"' in response.content.decode()
    assert draft(method).definition == before
    assert edit(client, method, "method_yaml", definition=definition(answer="sun")).status_code == 200
    assert "sun" in draft(method).definition


def test_an_edit_from_an_older_page_changes_nothing(client: Client, method: Method) -> None:
    draft = session(method, None)
    response = client.post(
        url(method, "method_answers"), {"revision": draft.revision - 1, "op": "add", "answer": "x"}, headers=HTMX
    )
    assert response.status_code == 409
    assert "This method changed in another tab." in response.content.decode()
    published = client.post(url(method, "method_publish"), {"revision": draft.revision - 1})
    assert published.status_code == 409
    assert answers(method) == []
    assert not method.versions.exists()


def test_publishing_makes_the_next_version_and_ends_the_draft(client: Client, method: Method) -> None:
    response = client.post(url(method, "method_publish"), {"revision": session(method, None).revision})
    assert response.headers["Location"] == url(method, "method_page", 1)
    assert not MethodDraft.objects.filter(method=method).exists()
    method.refresh_from_db()
    assert method.next_version == 2
    published = reverse("published", args=[method.pk])
    assert client.get(url(method, "method_page")).headers["Location"] == published
    assert client.get(published).headers["Location"] == url(method, "method_page", 1)
    assert client.post(reverse("method_edit", args=[method.pk])).status_code == 302
    edit(client, method, "method_yaml", definition=definition(answer="sun"))
    publish(client, method)
    assert list(method.versions.values_list("number", flat=True)) == [2, 1]
    assert "sun" not in method.versions.get(number=1).definition


def test_publishing_records_what_the_tests_gave_the_examples(
    client: Client, method: Method, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_answers", op="add", answer="dark")
    edit(client, method, "method_answers", op="example", row="1")
    assert "Run the tests to publish" in client.get(url(method, "method_page")).content.decode()
    assert "Run the tests first" in publish(client, method).content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        edit(client, method, "method_run")
    results = client.get(url(method, "method_results")).content.decode()
    assert re.search(r'id="method-publish" hx-swap-oob="outerMorph".*?>Publish</button>', results, re.DOTALL)
    assert "Run the tests to publish" not in results
    publish(client, method)
    assert "Results are up to date" in client.get(url(method, "method_page", 1)).content.decode(), "as tested"
    dark = Job.objects.get(kind=Job.Kind.DRAFT_TESTS).result[1]  # type: ignore[index]
    version = method.versions.get()
    published = BUILTINS.with_files([(method.full_name, version.definition)]).find(version.ref)
    [row] = published.examples[0].rows
    assert row.answers == {"Q1": "dark"}
    given = dark["results"]["Q1"]
    assert {key: said.model_dump() for key, said in row.expected.items()} == {
        "Q1": {"marks": given["marks"], "feedback": given["feedback"]}
    }
    guide = client.get(reverse("method_guide", args=["method-laboratory/light@1"]), headers=HTMX).content.decode()
    assert escape(given["feedback"]) in guide
    client.post(reverse("method_edit", args=[method.pk]))
    edit(client, method, "method_answers", op="add", answer="sun")
    assert "No changes to publish" in client.get(url(method, "method_page")).content.decode()


def test_a_draft_that_changes_nothing_isnt_published(client: Client, method: Method) -> None:
    publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    edit(client, method, "method_answers", op="add", answer="light")
    page = client.get(url(method, "method_page")).content.decode()
    assert "No changes to publish" in page
    assert "Nothing changed since version 1." in publish(client, method).content.decode()
    assert method.versions.count() == 1
    edit(client, method, "method_yaml", definition=definition(answer="sun"))
    assert "No changes to publish" not in client.get(url(method, "method_page")).content.decode()


def test_a_publish_that_fails_keeps_the_test_run_and_the_draft(
    client: Client, method: Method, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_run")
    edit(client, method, "method_answers", op="add", answer="dark")
    run = Job.objects.get(kind=Job.Kind.DRAFT_TESTS)
    before = draft(method).definition

    def refused(*args: object, **kwargs: object) -> None:
        raise ValidationError("A newer version was published since you started.")

    monkeypatch.setattr(designer_views, "publish", refused)
    assert "A newer version was published" in publish(client, method).content.decode()
    run.refresh_from_db()
    assert run.active
    assert draft(method).definition == before


def test_a_draft_with_problems_isnt_published(client: Client, method: Method) -> None:
    edit(client, method, "method_yaml", definition=definition(uses="gradeflow/nope@1"))
    page = publish(client, method).content.decode()
    assert 'role="alert"' in page
    assert "gradeflow/nope@1" in page
    assert not method.versions.exists()
    assert MethodDraft.objects.filter(method=method).exists()


def test_discarding_a_draft_keeps_what_was_published(client: Client, method: Method) -> None:
    publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    response = client.post(url(method, "method_discard"))
    assert response.headers["Location"] == reverse("published", args=[method.pk])
    assert method.versions.count() == 1
    assert not MethodDraft.objects.exists()


def test_discarding_an_unpublished_draft_discards_the_method(client: Client, course: Course, method: Method) -> None:
    other = User.objects.create_user("bo@example.edu", "another-password")
    Member.objects.create(course=course, user=other, role=Role.EDITOR)
    MethodDraft.objects.create(method=method, user=other, definition=draft(method).definition)
    client.post(url(method, "method_discard"))
    assert Method.objects.filter(pk=method.pk).exists(), "another person's draft keeps it"
    bo = signed_in(other)
    response = bo.post(url(method, "method_discard"))
    assert response.headers["Location"] == reverse("course_methods", args=[course.pk])
    assert not Method.objects.exists()


def test_a_version_is_tried_on_ones_own_answers_and_reset(
    client: Client, course: Course, method: Method, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    edit(client, method, "method_answers", op="add", answer="light")
    publish(client, method)
    page = client.get(url(method, "method_page", 1)).content.decode()
    assert "Version 1" in page
    assert ">Edit</button>" in page
    edit(client, method, "method_answers", 1, op="add", answer="dark")
    assert answers(method, 1) == [("light", False), ("dark", False)]
    with django_capture_on_commit_callbacks(execute=True):
        edit(client, method, "method_run", 1)
    assert Job.objects.get(kind=Job.Kind.VERSION_TESTS).status == Job.Status.COMPLETED
    assert edit(client, method, "method_answers", 1, op="example", row="0").status_code == 404
    assert edit(client, method, "method_yaml", 1, definition="checks: [").status_code == 200
    version = method.versions.get()
    assert version.tests["rows"] == [{"answers": {"Q1": "light"}, "example": False}]
    edit(client, method, "method_answers", 1, op="reset")
    assert answers(method, 1) == [("light", False)]
    other = User.objects.create_user("bo@example.edu", "another-password")
    Member.objects.create(course=course, user=other, role=Role.EDITOR)
    bo = signed_in(other)
    bo.get(url(method, "method_page", 1))
    assert VersionTests.objects.get(user=other).tests == version.tests, "each person tries it on their own"


def test_viewers_try_versions_but_change_no_method(client: Client, course: Course, method: Method) -> None:
    publish(client, method)
    other = User.objects.create_user("bo@example.edu", "another-password")
    Member.objects.create(course=course, user=other, role=Role.VIEWER)
    viewer = signed_in(other)
    page = viewer.get(url(method, "method_page", 1))
    assert page.status_code == 200
    assert ">Edit</button>" not in page.content.decode()
    assert "Add answer" in page.content.decode()
    revision = VersionTests.objects.get(user=other).revision
    response = viewer.post(url(method, "method_answers", 1), {"revision": revision, "op": "add", "answer": "x"})
    assert response.status_code == 200
    assert [row["answers"] for row in VersionTests.objects.get(user=other).tests["rows"]] == [{"Q1": "x"}]
    revision = VersionTests.objects.get(user=other).revision
    values = viewer.post(url(method, "method_values", 1), {"revision": revision, "op": "reset"})
    assert values.status_code == 404, "a version's input values are those it was published with"
    assert viewer.post(reverse("method_edit", args=[method.pk])).status_code == 403
    client.post(reverse("method_edit", args=[method.pk]))
    revision = session(method, None).revision
    assert viewer.post(url(method, "method_answers"), {"revision": revision, "op": "add"}).status_code == 404
    assert viewer.get(url(method, "method_page")).headers["Location"] == reverse("published", args=[method.pk])
    assert MethodDraft.objects.filter(user=other).count() == 0


def test_a_version_is_read_only_and_lists_the_others(client: Client, method: Method) -> None:
    publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    edit(client, method, "method_yaml", definition=definition(answer="sun"))
    publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    page = client.get(url(method, "method_page", 1)).content.decode()
    for target in (url(method, "method_page"), url(method, "method_page", 2), url(method, "method_page", 1)):
        assert f'<a href="{target}" hx-boost="false"' in page
    assert ">Open draft</button>" in page
    assert MarkingControls(page).enabled == []
    assert 'class="flex flex-1 flex-col read-only-fields"' in page
    drafted = client.get(url(method, "method_page"), {"path": "with.checks.0"}).content.decode()
    assert "points" in MarkingControls(drafted).enabled


def test_editing_an_older_version_starts_from_it_on_top_of_the_latest(client: Client, method: Method) -> None:
    publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]))
    edit(client, method, "method_yaml", definition=definition(answer="sun"))
    publish(client, method)
    client.post(reverse("method_edit", args=[method.pk]), {"number": "1"})
    started = draft(method)
    assert started.based_on == method.versions.get(number=2)
    assert "sun" not in started.definition
    assert publish(client, method).headers["HX-Redirect"] == url(method, "method_page", 3)
    assert (
        tree_of(method.versions.get(number=3).definition)["checks"]
        == tree_of(method.versions.get(number=1).definition)["checks"]
    )


def test_editing_needs_a_published_version(client: Client, method: Method) -> None:
    assert client.post(reverse("method_edit", args=[method.pk])).status_code == 404


def test_a_row_that_isnt_a_number_is_a_missing_row(client: Client, method: Method) -> None:
    response = edit(client, method, "method_answers", op="delete", row="first")
    assert response.status_code == 200
    assert "This answer is no longer there." in response.content.decode()


def test_a_checks_method_is_chosen_from_the_picker(client: Client, method: Method) -> None:
    check = {"path": "with.checks.0"}
    assert 'op=retype&amp;target=with.checks.0"' in client.get(url(method, "method_page"), check).content.decode()
    assert 'op=retype&amp;target="' not in client.get(url(method, "method_page")).content.decode()
    picker = client.get(url(method, "method_picker"), {"op": "retype", "target": "with.checks.0"})
    accepted = BUILTINS.find("gradeflow/accepted-answers@1")
    assert escape(accepted.label) in picker.content.decode()
    edit(
        client,
        method,
        "method_check",
        path="with.checks.0",
        op="retype",
        target="with.checks.0",
        value="gradeflow/accepted-answers@1",
    )
    assert tree_of(draft(method).definition)["checks"][0]["uses"] == "gradeflow/accepted-answers@1"


def test_a_version_has_no_draft_only_views(client: Client, method: Method) -> None:
    publish(client, method)
    for name in ("method_picker", "method_field", "method_publish", "method_discard"):
        assert client.post(url(method, name)).status_code in (404, 405), name


def test_a_run_of_answers_since_changed_is_out_of_date(
    client: Client, method: Method, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(queue, "executor", Idle)
    edit(client, method, "method_answers", op="add", answer="light")
    edit(client, method, "method_run")
    job = Job.objects.get(kind=Job.Kind.DRAFT_TESTS)
    edit(client, method, "method_answers", op="add", answer="dark")
    assert call_back(job, {"event": "finished"}) == 204
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.FAILED, "Out of date.")


def test_test_runs_are_forgotten_after_a_day(
    client: Client, method: Method, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    edit(client, method, "method_answers", op="add", answer="light")
    with django_capture_on_commit_callbacks(execute=True):
        edit(client, method, "method_run")
    job = Job.objects.get(kind=Job.Kind.DRAFT_TESTS)
    queue.recover_queue()
    assert Job.objects.filter(pk=job.pk).exists()
    Job.objects.filter(pk=job.pk).update(finished_at=timezone.now() - timedelta(days=2))
    queue.recover_queue()
    assert not Job.objects.filter(pk=job.pk).exists()


def test_a_new_method_from_your_methods_is_yours(client: Client, user: User) -> None:
    listing = client.get(reverse("your_methods")).content.decode()
    assert f'hx-post="{reverse("method_new")}"' in listing
    response = client.post(reverse("method_new"), new_method("Mine"), headers=HTMX)
    method = Method.objects.get()
    assert response.headers["HX-Redirect"] == url(method, "method_page")
    assert method.full_name == "ana-example-edu/mine"
    assert client.get(reverse("method_new")).status_code == 405


def test_a_new_method_isnt_called_what_another_is(client: Client, course: Course, method: Method) -> None:
    publish(client, method)
    response = client.post(new_url(course), new_method("light"), headers=HTMX)
    assert response.status_code == 422
    assert "This library already has a method called “Light”." in response.content.decode()
    assert re.search(r'name="label"[^>]*aria-invalid="true"', response.content.decode()), "on the name field"
    assert Method.objects.count() == 1


def test_an_unpublished_method_is_only_its_authors(client: Client, course: Course, method: Method) -> None:
    other = User.objects.create_user("bo@example.edu", "another-password")
    Member.objects.create(course=course, user=other, role=Role.EDITOR)
    published = reverse("published", args=[method.pk])
    assert client.get(published).headers["Location"] == url(method, "method_page")
    assert signed_in(other).get(published).status_code == 404


def test_the_picker_for_a_check_thats_gone_isnt_found(client: Client, method: Method) -> None:
    assert client.get(url(method, "method_picker"), {"op": "retype", "target": "with.checks.5"}).status_code == 404


def test_a_methods_problem_with_a_test_value_names_the_input_by_its_title() -> None:
    inputs = {"min_value": {"title": "Lower limit"}, "untitled": {}}
    said = designer_presenters._said
    assert said(Problem("Must be a number.", field=("min_value",)), inputs) == "Lower limit: Must be a number."
    assert said(Problem("Must be a number.", field=("untitled",)), inputs) == "untitled: Must be a number."
    assert said(Problem("Add at least one question."), inputs) == "Add at least one question."
