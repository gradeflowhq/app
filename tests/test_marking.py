import html
import json
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape
from gradeflow_core import GradeFlowError, InvalidError, MarkScheme, Methods, Question, QuestionSet
from gradeflow_core.editing import DECISIONS, Decision, MarkingDraft, dump_yaml
from jsonpointer import JsonPointer
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.editor.editing import problem_text
from gradeflow_app.editor.presenters import editor
from gradeflow_app.imports import models as import_models
from gradeflow_app.libraries.collection import course_methods
from gradeflow_app.live.topics import topic
from gradeflow_app.marking import checks
from gradeflow_app.marking import workspace as marking_workspace
from gradeflow_app.marking.answers import answer_groups, column
from gradeflow_app.marking.answers.comparisons import GROUPING
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.questions import models as question_models
from gradeflow_app.ui.middleware import MESSAGES_HEADER
from gradeflow_app.ui.templatetags.ui import markdown
from tests.conftest import (
    HTMX_HEADERS,
    FakeRunner,
    between,
    code_draft,
    encrypted_id,
    full_marks_marking,
    make_code_question,
    preview_draft,
    save_draft,
    start_marking,
)

pytestmark = pytest.mark.django_db


def definition_tree(draft: MarkingDraftRecord) -> dict[str, Any]:
    draft.refresh_from_db()
    return definition_data(draft.definition)


def save_definition(draft: MarkingDraftRecord, definition: dict[str, Any]) -> None:
    draft.definition = dump_yaml(definition)
    draft.save()


def accepted_answers_check(question: str, answer: str) -> dict[str, Any]:
    return {"question": question, "uses": "gradeflow/accepted-answers@1", "with": {"answers": [answer]}}


def problem_message(draft: MarkingDraftRecord, part: str | int) -> str:
    """What the core says is wrong with the draft's field whose location holds `part`."""
    draft.refresh_from_db()
    return escape(
        next(
            problem.message
            for problem in MarkingWorkspace(draft).draft.problems
            if str(part) in JsonPointer(problem.field).parts
        )
    )


def question_group_marking(assessment: Assessment) -> Marking:
    conditional = {
        "uses": "gradeflow/conditional@1",
        "with": {"condition": [accepted_answers_check("Q1", "light")], "then": [accepted_answers_check("Q2", "42")]},
    }
    return Marking.objects.create(assessment=assessment, definition=dump_yaml(conditional))


def workspace_of(assessment: Assessment, definition: dict[str, Any], key: str | None = "Q1") -> MarkingWorkspace:
    question = assessment.questions.get(key=key) if key else None
    user = assessment.course.members.get().user
    return MarkingWorkspace(
        MarkingDraftRecord(assessment=assessment, question=question, user=user, definition=dump_yaml(definition))
    )


def test_a_typing_post_reads_the_answers_once(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    keys_read: list[str] = []
    answer_expression = import_models.answer_expression

    def recording_answer_expression(key: str) -> Any:
        keys_read.append(key)
        return answer_expression(key)

    monkeypatch.setattr("gradeflow_app.marking.workspace.answer_expression", recording_answer_expression)
    response = client.post(url, {"path": "", "fields_posted": "1", "points": ["light"], "marks_by": "all_correct"})
    assert response.status_code == 200
    assert keys_read == ["Q1"]


def test_stale_or_unknown_editor_actions_change_nothing(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/workflow@1")
    client.post(url, {"path": "", "operation": "add", "target": "/with/steps/0", "method": "gradeflow/key-points@1"})
    client.post(url, {"path": "", "operation": "remove", "target": "/with/steps/0"})
    before = definition_tree(draft)
    stale_actions = [
        {"path": "", "operation": "remove", "target": "/with/steps/0"},
        {"path": "", "operation": "move", "target": "/with/steps/3", "direction": "down"},
        {"path": "", "operation": "add", "target": "/with/nowhere/0", "method": "gradeflow/key-points@1"},
        {"path": "", "operation": "open", "target": "/with/steps/7"},
        {"path": "/with/steps/4", "fields_posted": "1", "points": ["light"]},
        {"path": "", "operation": "change_method", "target": "", "method": "gradeflow/nope@1"},
        {"path": "", "operation": "add", "target": "/with/steps/0", "method": "gradeflow/nope@1"},
    ]
    for data in stale_actions:
        assert client.post(url, data).status_code == 200, data
    assert definition_tree(draft) == before
    assert client.get(url, {"path": "/with/setup"}).status_code == 200


def test_questions_opens_the_first_question(client: Client, assessment: Assessment) -> None:
    """Marked or not. A page load goes to its address; a click in the app gets it at once, with its address."""
    full_marks_marking(assessment, "Q1")
    url = reverse("questions", args=[assessment.pk])
    first = reverse("question", args=[assessment.pk, assessment.questions.get(key="Q1").pk])
    assert client.get(url).headers["Location"] == first
    clicked = client.get(url, headers=HTMX_HEADERS)
    assert clicked.headers["HX-Push-Url"] == first
    assert clicked.context["question"].key == "Q1"


def test_unknown_methods_are_not_found(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    start = reverse("marking_start", args=[assessment.pk, question.pk])
    assert client.post(start, {"method": "gradeflow/nope@1"}).status_code == 404
    assert client.post(start, {"method": "gradeflow/conditional@1"}).status_code == 404, "it marks question groups"
    assert (
        client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/nope@1"}).status_code
        == 404
    )
    assert client.post(reverse("question_group_new", args=[assessment.pk])).status_code == 404


def test_a_removed_marking_can_be_restored_from_its_history(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    client.post(url, {"path": "", "fields_posted": "1", "points": ["light"], "marks_by": "all_correct"})
    question = draft.question
    assert question is not None
    save_draft(client, draft)
    history = reverse("marking_history", args=[assessment.pk, question.pk])
    assert "Current" in client.get(history).text
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("marking_remove", args=[assessment.pk, question.pk]))
    assert not Marking.objects.exists()
    versions = client.get(history).text
    assert "Current" not in versions
    assert "Restore" in versions
    assert history in client.get(reverse("question", args=[assessment.pk, question.pk])).text


def test_typing_in_a_check_says_the_draft_is_saved(client: Client, assessment: Assessment) -> None:
    url, _ = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    response = client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]})
    assert '<span id="editor-status" hx-swap-oob="true" class="text-sm text-muted">Draft saved</span>' in response.text


def test_restoring_a_version_drops_the_preview_of_the_draft_it_replaces(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]})
    save_draft(client, draft)
    question = assessment.questions.get(key="Q2")
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    draft = MarkingDraftRecord.objects.get()
    client.post(reverse("draft", args=[assessment.pk, draft.pk]), {"path": "", "fields_posted": "1", "answers": ["41"]})
    preview_draft(client, draft)
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    changed_option = '<option value="changed"'
    assert changed_option in client.get(answers_url).text
    version = Marking.history.get().history_id
    client.post(reverse("marking_restore", args=[assessment.pk, question.pk, version]))
    assert changed_option not in client.get(answers_url).text


def test_while_previewing_the_marks_filters_follow_the_previewed_marks(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]})
    save_draft(client, draft)
    question = assessment.questions.get(key="Q2")
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    draft = MarkingDraftRecord.objects.get()
    client.post(reverse("draft", args=[assessment.pk, draft.pk]), {"path": "", "fields_posted": "1", "answers": ["41"]})
    preview_draft(client, draft)
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    full = client.get(answers_url, {"filter": "full", "group": "answer"}, headers=HTMX_HEADERS).text
    assert "1 student<" in full, "41, given full marks by the preview"
    assert "5 students" not in full, "42, full marks only in the saved marking"
    no_marks = client.get(answers_url, {"filter": "no_marks", "group": "answer"}, headers=HTMX_HEADERS).text
    assert "5 students" in no_marks


def test_questions_cache_validation_and_markings_use_the_supplied_collection(
    client: Client, assessment: Assessment
) -> None:
    question = assessment.questions.get(key="Q2")
    assert question.core_question is question.core_question
    question.definition = {**question.definition, "max_marks": 3}
    assert question.core_question.max_marks == 3
    definition = dump_yaml(accepted_answers_check("Q2", "42"))
    methods = course_methods(assessment.course)
    checked = checks.check_of(definition, methods)
    assert checked is not None
    assert checked.dump() == accepted_answers_check("Q2", "42")
    without = Methods(method for method in methods if method.ref != "gradeflow/accepted-answers@1")
    assert checks.check_of(definition, without) is None


def test_a_new_question_group_asks_for_what_it_needs(client: Client, assessment: Assessment) -> None:
    started = client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    page = client.get(started.headers["Location"])
    assert page.status_code == 200
    assert b"None yet" in page.content
    assert b"Required." not in page.content, "an empty new list isn't a mistake yet"
    draft = MarkingDraftRecord.objects.get()
    saving = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "operation": "save"})
    saved = saving.text
    assert 'role="alert"' not in saved
    assert "Add at least one step." in between(saved, 'id="steps-setting-1"', "</section>"), "on the empty list"


def test_a_problem_with_the_assessment_shows_on_the_field_at_fault(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q3", "gradeflow/multiple-choice@1")
    save_definition(draft, {"question": "Q3", "uses": "gradeflow/multiple-choice@1", "with": {"correct": ["a", "e"]}})
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).text
    errors = between(page, 'id="id_correct_error"', "</div>")
    assert problem_message(draft, "correct") in errors


def post_parameter_rows(url: str, client: Client, fields: dict[str, str], rows: list[tuple[str, str, str]]) -> str:
    row_fields = {"parameters-posted": "1"}
    for index, (key, tag, value) in enumerate(rows):
        row_fields |= {
            f"parameters-{index}-key": key,
            f"parameters-{index}-tag": tag,
            f"parameters-{index}-value": value,
        }
    return client.post(url, fields | row_fields).text


def test_parameters_are_named_and_checked_as_the_core_says(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields | {"operation": "add_row", "target": "parameters"})
    page = client.post(url, fields | {"operation": "add_row", "target": "parameters"}).text
    draft = MarkingDraftRecord.objects.get()
    assert list(definition_tree(draft)["with"]["parameters"]) == ["parameter_1", "parameter_2"]
    assert "Add parameter" in page
    assert 'pattern="^[A-Za-z_][A-Za-z0-9_]*$"' in page
    rows = [("1st", "integer", "2"), ("parameter_2", "integer", "0.5")]
    page = post_parameter_rows(url, client, fields | {"fields_posted": "1"}, rows)
    first = between(page, 'id="parameters-0-error"', "</div>")
    assert problem_message(draft, "1st") in first
    assert problem_message(draft, "parameter_2") in between(page, 'id="parameters-1-error"', "</div>")
    assert definition_tree(draft)["with"]["parameters"]["parameter_2"]["value"] == "0.5", (
        "kept as typed, for the core to check"
    )


def test_a_new_parameter_never_replaces_another(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    for _ in range(2):
        client.post(url, fields | {"operation": "add_row", "target": "parameters"})
    client.post(url, fields | {"operation": "remove_row", "target": "parameters", "value": "parameter_1"})
    client.post(url, fields | {"operation": "add_row", "target": "parameters"})
    assert len(definition_tree(MarkingDraftRecord.objects.get())["with"]["parameters"]) == 2


def test_parameters_the_editor_cant_edit_are_kept_as_they_are(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    draft = MarkingDraftRecord.objects.get()
    listed = {"type": "list", "value": [{"type": "integer", "value": 1}]}
    definition = definition_tree(draft)
    definition["with"]["parameters"] = {"sizes": listed, "n": {"type": "integer", "value": 1}}
    save_definition(draft, definition)
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).text
    assert "Can't be edited here" in page
    row_fields = {"parameters-posted": "1", "parameters-0-key": "sizes", "parameters-1-key": "n"}
    row_fields |= {"parameters-1-tag": "integer", "parameters-1-value": "2"}
    client.post(url, fields | {"fields_posted": "1"} | row_fields)
    assert definition_tree(draft)["with"]["parameters"] == {"sizes": listed, "n": {"type": "integer", "value": 2}}


def test_a_parameter_is_removed_by_the_name_its_row_has_now(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    draft = MarkingDraftRecord.objects.get()
    definition = definition_tree(draft)
    definition["with"]["parameters"] = {"n": {"type": "integer", "value": 1}}
    save_definition(draft, definition)
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).text
    (remove_values,) = re.findall(r'hx-vals="([^"]*remove_row[^"]*)"', page)
    row = {
        "parameters-posted": "1",
        "parameters-0-key": 'a"b',
        "parameters-0-tag": "integer",
        "parameters-0-value": "1",
    }
    client.post(url, fields | row | json.loads(html.unescape(remove_values)))
    assert "parameters" not in definition_tree(draft)["with"], "none left, as by default"


def test_a_saved_marking_the_core_now_rejects_opens_and_says_so(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    _, fields = code_draft(client, assessment)
    client.post(
        reverse("draft_save", args=[assessment.pk, MarkingDraftRecord.objects.get().pk]), fields | {"operation": "save"}
    )
    marking = Marking.objects.get()
    definition = definition_data(marking.definition)
    definition["with"]["parameters"] = {"answer": {"type": "integer", "value": 1}}
    marking.definition = dump_yaml(definition)
    marking.save()
    question = marking.question
    assert question is not None
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).text
    assert "This marking isn't compatible with the current GradeFlow" in page
    settings_url = reverse("question_settings", args=[assessment.pk, question.pk])
    with django_capture_on_commit_callbacks(execute=True):
        saved = client.post(settings_url, {"question_type": "text", "save": "1", "max_marks": "2"})
    assert saved.status_code == 200, "marking the question again skips what can't mark"
    started = client.post(reverse("marking_start", args=[assessment.pk, question.pk])).text
    assert problem_message(MarkingDraftRecord.objects.get(), "answer") in started
    assert Marking.objects.filter(pk=marking.pk).exists(), "kept until it's fixed"


def test_breadcrumbs_name_each_check_as_its_card_does(assessment: Assessment) -> None:
    points = {"name": "Light", "uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}
    workflow = {
        "question": "Q1",
        "uses": "gradeflow/workflow@1",
        "with": {"steps": [points, {"uses": "gradeflow/not-allowed@1", "with": {}}]},
    }
    workspace = workspace_of(assessment, workflow)
    for path in ("/with/steps/0", "/with/steps/1"):
        breadcrumbs = editor(workspace, path).breadcrumbs
        assert breadcrumbs == [
            (breadcrumb_path, workspace.draft.label(breadcrumb_path)) for breadcrumb_path in ("", path)
        ]


def test_a_check_inside_a_groups_check_marks_that_checks_question(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    draft = MarkingDraftRecord.objects.get()
    url = reverse("draft", args=[assessment.pk, draft.pk])
    picker = client.get(
        reverse("draft_methods", args=[assessment.pk, draft.pk]),
        {"operation": "add", "target": "/with/then/0", "question": "Q3"},
    ).text
    assert "Choose a method for Q3" in picker
    assert '<input type="hidden" name="question" value="Q3">' in picker
    add_step_fields = {
        "path": "",
        "operation": "add",
        "target": "/with/then/0",
        "method": "gradeflow/workflow@1",
        "question": "Q3",
    }
    assert client.post(url, add_step_fields).status_code == 200
    add_inner_step_fields = {
        "path": "/with/then/0",
        "operation": "add",
        "target": "/with/then/0/with/steps/0",
        "method": "gradeflow/multiple-choice@1",
    }
    client.post(url, add_inner_step_fields)
    open_response = client.post(url, {"path": "", "operation": "open", "target": "/with/then/0/with/steps/0"})
    assert open_response.status_code == 200
    assert 'name="correct"' in open_response.text, "Q3's options to choose from"


def test_a_question_in_a_group_names_the_others(client: Client, assessment: Assessment) -> None:
    question_group_marking(assessment)
    question = assessment.questions.get(key="Q2")
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).text
    assert "Question group Q1, Q2" in page


def test_checks_are_moved_and_removed(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/workflow@1")
    for index, method in enumerate(("gradeflow/key-points@1", "gradeflow/accepted-answers@1")):
        client.post(url, {"path": "", "operation": "add", "target": f"/with/steps/{index}", "method": method})
    client.post(url, {"path": "", "operation": "move", "target": "/with/steps/0", "direction": "down"})
    assert [check["uses"] for check in definition_tree(draft)["with"]["steps"]] == [
        "gradeflow/accepted-answers@1",
        "gradeflow/key-points@1",
    ]
    page = client.post(url, {"path": "/with/steps/1", "operation": "remove", "target": "/with/steps/1"}).text
    assert [check["uses"] for check in definition_tree(draft)["with"]["steps"]] == ["gradeflow/accepted-answers@1"]
    assert 'name="path" value=""' in page, "removing the check shown goes back to what holds it"


def test_rows_are_added_and_removed(client: Client, assessment: Assessment) -> None:
    make_code_question(assessment, "Q1")
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/code-tests@1")
    for _ in range(2):
        client.post(url, {"path": "", "operation": "add_row", "target": "tests"})
    assert definition_tree(draft)["with"]["tests"] == [{}, {}]
    rows = {"tests-posted": "1"}
    for index, expression in enumerate(("add(1, 2)", "add(2, 2)")):
        rows |= {f"tests-{index}-expression": expression, f"tests-{index}-expected": "3"}
    client.post(
        url, {"path": "", "fields_posted": "1", "operation": "remove_row", "target": "tests", "value": "0"} | rows
    )
    assert definition_tree(draft)["with"]["tests"] == [{"expression": "add(2, 2)", "expected": "3"}]


def test_problems_with_rows_show_on_the_row_at_fault(client: Client, assessment: Assessment) -> None:
    make_code_question(assessment, "Q1")
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/code-tests@1")
    client.post(url, {"path": "", "operation": "add_row", "target": "tests"})
    saving = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "operation": "save"})
    errors = between(saving.text, 'id="id_tests-0-expression_error"', "</div>")
    assert "text-error" in errors


def test_a_cleared_text_field_is_cleared(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/pattern@1")
    for typed in ("a+", ""):
        client.post(url, {"path": "", "fields_posted": "1", "pattern": typed})
        assert definition_tree(draft)["with"]["pattern"] == typed


def test_numbers_the_core_rejects_say_why_on_their_field(client: Client, assessment: Assessment) -> None:
    make_code_question(assessment, "Q1")
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/code-tests@1")
    for typed in ("0", "soon"):
        page = client.post(url, {"path": "", "fields_posted": "1", "setup__time_limit": typed}).text
        errors = between(page, 'id="id_setup__time_limit_error"', "</div>")
        assert "text-error" in errors, typed
        assert definition_tree(draft)["with"]["setup"]["time_limit"] == (0 if typed == "0" else typed), "kept as typed"


def htmx_page(client: Client, url: str) -> str:
    return client.get(url, headers=HTMX_HEADERS).text


def test_a_question_groups_answers_show_each_option(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q3")
    question.core_question = Question.load({**question.definition, "allow_multiple": True})
    question.save()
    choice_check = {"question": "Q3", "uses": "gradeflow/multiple-choice@1", "with": {"correct": ["a"]}}
    conditional = {
        "uses": "gradeflow/conditional@1",
        "with": {"condition": [accepted_answers_check("Q1", "light")], "then": [choice_check]},
    }
    marking = Marking.objects.create(assessment=assessment, definition=dump_yaml(conditional))
    page = htmx_page(client, reverse("question_group_answers", args=[assessment.pk, marking.pk]))
    assert set(re.findall(r'<li class="badge[^"]*" title="Options">([^<]*)</li>', page)) == {"a", "b", "c", "d"}


def test_a_question_groups_answers_show_by_combination(client: Client, assessment: Assessment) -> None:
    marking = question_group_marking(assessment)
    saved = htmx_page(client, reverse("question_group_answers", args=[assessment.pk, marking.pk]))
    assert ">Q2</h2>" in saved
    assert "Read only" in saved, "the condition's question"
    assert 'class="preview-bar"' not in saved
    client.post(reverse("question_group_edit", args=[assessment.pk, marking.pk]))
    editing = htmx_page(client, reverse("question_group_answers", args=[assessment.pk, marking.pk]))
    assert 'class="preview-bar"' not in editing, "nothing to preview until the draft changes"
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    new_draft = MarkingDraftRecord.objects.get(marking=None)
    new_draft_answers = htmx_page(client, reverse("question_group_draft_answers", args=[assessment.pk, new_draft.pk]))
    assert "No questions yet" in new_draft_answers
    assert reverse("question_group_draft_answers", args=[assessment.pk, new_draft.pk]) in new_draft_answers


def test_restoring_a_version_opens_it_as_a_draft(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["light"]})
    save_draft(client, draft)
    question = assessment.questions.get(key="Q1")
    version = Marking.history.get().history_id
    restored = client.post(reverse("marking_restore", args=[assessment.pk, question.pk, version]), headers=HTMX_HEADERS)
    assert json.loads(restored.headers["HX-Location"])["path"] == reverse("question", args=[assessment.pk, question.pk])
    assert definition_tree(MarkingDraftRecord.objects.get(question=question))["with"]["answers"] == ["light"]


def test_a_draft_says_when_someone_saved_the_marking_since_it_started(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["light"]})
    save_draft(client, draft)
    question = assessment.questions.get(key="Q1")
    page_url = reverse("question", args=[assessment.pk, question.pk])
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    notice = "Someone saved this marking after you started."
    assert notice not in client.get(page_url).text
    Marking.objects.get().save()
    assert notice in client.get(page_url).text


def current_links(client: Client, page_url: str) -> list[str]:
    """What the question list, refreshed in place on `page_url`, shows as current, by link."""
    rendered = client.get(page_url).text
    refresh = re.search(r'<nav id="question-list"[^>]*hx-get="([^"]*)"', rendered)
    assert refresh is not None
    listed = client.get(html.unescape(refresh.group(1)), headers=HTMX_HEADERS).text
    return re.findall(r'href="([^"]*)" class="question-link[^"]*" aria-current="page"', listed)


def test_the_question_list_highlights_what_the_page_shows(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    marking = question_group_marking(assessment)
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    draft = MarkingDraftRecord.objects.get()
    for page_url in (
        reverse("question", args=[assessment.pk, question.pk]),
        reverse("question_group", args=[assessment.pk, marking.pk]),
        reverse("question_group_draft", args=[assessment.pk, draft.pk]),
        reverse("question_group_new", args=[assessment.pk]),
    ):
        assert current_links(client, page_url) == [page_url]


def test_the_question_list_validates_each_marking_once(client: Client, assessment: Assessment) -> None:
    question_group_marking(assessment)
    from unittest.mock import patch

    with patch.object(MarkScheme, "load", wraps=MarkScheme.load) as load_spy:
        client.get(reverse("question_list", args=[assessment.pk]), headers=HTMX_HEADERS)
    assert load_spy.call_count == 1


def test_marking_an_answer_redraws_the_editor_and_the_answers(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    page = client.post(url, {"decision": "full_marks", "answer": "42"}, headers=HTMX_HEADERS).text
    assert 'id="marking-panel"' in page
    assert 'id="answers"' in page.split('id="marking-panel"', 1)[0]
    assert 'hx-swap-oob="outerMorph"' in page


def test_the_way_back_opens_what_holds_the_check(client: Client, assessment: Assessment) -> None:
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/workflow@1")
    client.post(url, {"path": "", "operation": "add", "target": "/with/steps/0", "method": "gradeflow/key-points@1"})
    page = client.post(url, {"path": "/with/steps/0", "operation": "open", "target": ""}).text
    assert 'name="path" value=""' in page


def test_too_many_answers_to_compare_are_grouped_when_equal(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch, fake_runner: FakeRunner
) -> None:
    monkeypatch.setattr(answer_groups, "MAX_COMPARED", 2)
    url = reverse("answers", args=[assessment.pk, assessment.questions.get(key="Q1").pk])
    for grouping in ("spelling", "meaning"):
        page = client.get(url, {"group": grouping}, headers=HTMX_HEADERS).text
        assert "Too many answers to compare. Showing the same answers only." in page, grouping
    assert not fake_runner.run_ids(GROUPING), "nothing to compare"
    assert "Too many answers" not in client.get(url, {"group": "answers"}, headers=HTMX_HEADERS).text


def test_more_answer_groups_show_on_request_until_the_list_changes(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(column, "ANSWER_GROUPS_PER_PAGE", 4)
    url = reverse("answers", args=[assessment.pk, assessment.questions.get(key="Q1").pk])

    def shown(**query: str) -> tuple[int, bool]:
        page = client.get(url, query, headers=HTMX_HEADERS).text
        return page.count('<details class="group/answer'), "Show more" in page

    assert shown() == (4, True)
    assert shown(shown_count="8") == (8, True)
    assert shown() == (8, True)
    assert shown(shown_count="12") == (10, False)
    assert shown(filter="all") == (4, True)


def test_a_values_check_suggests_that_values_answers(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    question.core_question = Question.load(
        {"type": "values", "value_types": ["text", "text"], "max_marks": question.core_question.max_marks}
    )
    question.save()
    assessment.students.filter(encrypted_id=encrypted_id("S001")).update(answers={"Q1": "light, water"})
    checks = [
        {"input": f"{{{{ values[{index}] }}}}", "uses": "gradeflow/accepted-answers@1", "with": {"answers": []}}
        for index in range(2)
    ]
    values = {"question": "Q1", "uses": "gradeflow/multiple-values@1", "with": {"checks": checks}}
    workspace = workspace_of(assessment, values)
    assert workspace.suggestions_at(workspace.draft.position("/with/checks/1")) == {"water": 1}


def test_answers_the_question_reads_as_blank_arent_suggested(assessment: Assessment) -> None:
    assessment.students.filter(encrypted_id=encrypted_id("S001")).update(answers={"Q1": "N/A"})
    workspace = workspace_of(assessment, {"question": "Q1", "uses": "gradeflow/accepted-answers@1"})
    suggestions = workspace.suggestions_at(workspace.draft.position(""))
    assert "N/A" not in suggestions
    assert "respiration" in suggestions


def test_a_question_groups_checks_suggest_their_questions_answers(client: Client, assessment: Assessment) -> None:
    marking = question_group_marking(assessment)
    client.post(reverse("question_group_edit", args=[assessment.pk, marking.pk]))
    url = reverse("draft", args=[assessment.pk, MarkingDraftRecord.objects.get().pk])

    def opened(target: str) -> str:
        return client.post(url, {"path": "", "operation": "open", "target": target}).text

    assert '<option value="respiration" data-data=\'{"count": 1}\'>' in opened("/with/condition/0")
    assert '<option value="41" data-data=\'{"count": 1}\'>' in opened("/with/then/0")


def test_the_marking_opens_from_its_breadcrumb_whose_path_is_empty(client: Client, assessment: Assessment) -> None:
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/workflow@1")
    client.post(url, {"path": "", "operation": "add", "target": "/with/steps/0", "method": "gradeflow/key-points@1"})
    page = client.post(url, {"path": "/with/steps/0", "operation": "open"})
    assert page.status_code == 200
    assert 'name="path" value=""' in page.text


def test_a_question_groups_check_is_added_by_question_then_method(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    draft = MarkingDraftRecord.objects.get()
    url = reverse("draft", args=[assessment.pk, draft.pk])
    page = client.post(url, {"path": "", "operation": "open", "target": ""}).text
    menu = [html.unescape(each) for each in re.findall(r'hx-get="([^"]*target=/with/condition/0[^"]*)"', page)]
    assert [each.rsplit("question=", 1)[1] for each in menu] == ["Q1", "Q2", "Q3", ""], "then several questions"
    again = client.get(menu[2]).text
    assert "Choose a method for Q3" in again
    assert '<input type="hidden" name="target" value="/with/condition/0">' in again
    assert '<input type="hidden" name="path" value="">' in again
    assert 'data-method="gradeflow/multiple-choice@1"' in again, "Q3's methods"


def test_a_long_list_of_questions_to_add_a_check_for_has_a_search_field(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/workflow@1"})
    url = reverse("draft", args=[assessment.pk, MarkingDraftRecord.objects.get().pk])
    assert "Nothing matches." not in client.post(url, {"path": "", "operation": "open"}).text
    question_models.Question.objects.bulk_create(
        question_models.Question(assessment=assessment, key=f"Q{number}", position=number, definition={"type": "text"})
        for number in range(4, 10)
    )
    assert "Nothing matches." in client.post(url, {"path": "", "operation": "open"}).text


def test_a_question_groups_check_may_be_a_method_of_several_questions(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/workflow@1"})
    draft = MarkingDraftRecord.objects.get()
    methods = reverse("draft_methods", args=[assessment.pk, draft.pk])
    page = client.post(reverse("draft", args=[assessment.pk, draft.pk]), {"path": "", "operation": "open"}).text
    assert "Several questions" in page
    first_question_picker = client.get(methods, {"operation": "add", "target": "/with/steps/0", "path": ""}).text
    assert 'data-method="gradeflow/answer-sets@1"' not in first_question_picker, "the first question's methods"
    several_questions_picker = client.get(
        methods, {"operation": "add", "target": "/with/steps/0", "path": "", "question": ""}
    ).text
    assert "Choose a method for several questions" in several_questions_picker
    assert 'data-method="gradeflow/answer-sets@1"' in several_questions_picker
    add_step_fields = {
        "path": "",
        "operation": "add",
        "target": "/with/steps/0",
        "method": "gradeflow/answer-sets@1",
        "question": "",
    }
    client.post(reverse("draft", args=[assessment.pk, draft.pk]), add_step_fields)
    draft.refresh_from_db()
    assert "question" not in draft.definition.split("answer-sets", 1)[1]


def test_a_question_has_one_marking_but_a_draft_marking_it_again_previews(
    client: Client, assessment: Assessment
) -> None:
    Marking.objects.create(
        assessment=assessment,
        question=assessment.questions.get(key="Q2"),
        definition=dump_yaml(accepted_answers_check("Q2", "42")),
    )
    marking = question_group_marking(assessment)
    client.post(reverse("question_group_edit", args=[assessment.pk, marking.pk]))
    draft = MarkingDraftRecord.objects.get()
    draft.definition = dump_yaml({**definition_data(draft.definition), "name": "Edited group"})
    draft.save(update_fields=["definition"])
    saving = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "operation": "save"})
    assert "Q2 has another marking. A question has one marking." in saving.text
    assert f'href="{reverse("question", args=[assessment.pk, assessment.questions.get(key="Q2").pk])}"' in saving.text
    assert MarkingDraftRecord.objects.filter(pk=draft.pk).exists()
    assert "Edited group" not in Marking.objects.get(pk=marking.pk).definition
    preview_draft(client, draft)
    draft.refresh_from_db()
    assert draft.preview_results is not None, "a preview saves nothing, so it needn't wait for the other marking"


def test_question_group_validation_refreshes_when_another_marking_releases_its_question(
    client: Client, assessment: Assessment
) -> None:
    question = assessment.questions.get(key="Q2")
    Marking.objects.create(
        assessment=assessment, question=question, definition=dump_yaml(accepted_answers_check("Q2", "42"))
    )
    marking = question_group_marking(assessment)
    client.post(reverse("question_group_edit", args=[assessment.pk, marking.pk]))
    draft = MarkingDraftRecord.objects.get()
    draft.definition = dump_yaml({**definition_data(draft.definition), "name": "Edited group"})
    draft.save(update_fields=["definition"])
    page = client.get(reverse("question_group", args=[assessment.pk, marking.pk])).text
    status_url = reverse("draft", args=[assessment.pk, draft.pk], query={"status_only": 1})
    assert status_url in page
    assert 'hx-include="#marking-form [name=path]"' in page
    before = client.get(status_url).text
    assert "Q2 has another marking" in before
    original = draft.definition
    client.post(reverse("marking_remove", args=[assessment.pk, question.pk]))
    after = client.get(status_url).text
    assert "Q2 has another marking" not in after
    assert 'id="preview-bar"' in after
    assert 'id="marking-form"' not in after
    draft.refresh_from_db()
    assert draft.definition == original
    preview_draft(client, draft)
    page = client.get(status_url).text
    assert "students&#x27; results change" in page or "students' results change" in page
    assert 'id="marking-form"' not in page
    client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "operation": "save"})
    assert client.get(status_url).status_code == 204
    assert client.get(reverse("draft", args=[assessment.pk, draft.pk])).status_code == 404


def test_a_draft_is_edited_as_yaml_with_problems_on_their_lines(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    assert "Edit as YAML" in client.get(url).text
    yaml_url = reverse("draft_yaml", args=[assessment.pk, draft.pk])
    assert "uses: gradeflow/key-points@1" in client.get(yaml_url).text
    workspace = MarkingWorkspace(draft)

    def shows_what_the_core_says(response: Any, text: str) -> None:
        try:
            said = [
                str(problem)
                for problem in MarkingDraft.from_yaml(
                    text, workspace.questions, "Q1", methods=workspace.methods
                ).problems
            ]
        except InvalidError as error:
            said = [str(problem) for problem in error.problems]
        assert said
        assert all(escape(problem) in response.text for problem in said)

    typed = "question: Q1\nuses: gradeflow/key-points@1\nwith:\n  points: []\n"
    wrong = client.post(yaml_url, {"definition": typed}, headers=HTMX_HEADERS)
    assert wrong.status_code == 422
    shows_what_the_core_says(wrong, typed)
    assert definition_tree(draft)["with"] == {"points": []}, "kept as typed, to fix in either editor"
    assert "Saved to your draft" in wrong.text
    assert 'id="marking-panel" class="flex min-h-full flex-col" hx-swap-oob' in wrong.text
    unparsable = client.post(yaml_url, {"definition": "uses: [\n"}, headers=HTMX_HEADERS)
    shows_what_the_core_says(unparsable, "uses: [\n")
    assert "Not saved" in unparsable.text
    assert "marking-panel" not in unparsable.text
    unnamed = "uses: gradeflow/key-points@1\nwith:\n  points: [light]\n"
    fixed = client.post(yaml_url, {"definition": unnamed}, headers=HTMX_HEADERS)
    assert fixed.headers["HX-Refresh"] == "true"
    assert definition_tree(draft) == {"question": "Q1", "uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}


def marked_by_example(draft: MarkingDraftRecord, answer: str, decision: Decision) -> dict[str, Any]:
    """The draft as the core marks `answer` by example, written as the draft is saved."""
    draft.refresh_from_db()
    workspace = MarkingWorkspace(draft)
    workspace.draft.mark_by_example([answer], decision)
    return MarkScheme.load([workspace.draft.data], methods=workspace.methods).dump()[0]


def decision_count(draft: MarkingDraftRecord) -> int:
    """How many answers the core says the draft decided, each of which the answers column offers to clear."""
    draft.refresh_from_db()
    workspace = MarkingWorkspace(draft)
    return len(workspace.draft.answer_decisions())


def test_an_answer_group_is_marked_by_example(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields_posted": "1", "answers": ["42"]})
    question = assessment.questions.get(key="Q2")
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    answers = client.get(answers_url).text
    assert all(f'value="{decision}">{label}</button>' in answers for decision, label in DECISIONS.items())
    assert answers.count('value="cancel">') == decision_count(draft)
    example = reverse("mark_example", args=[assessment.pk, question.pk])
    expected_tree = marked_by_example(draft, "41", "partial_marks")
    page = client.post(example, {"answer": ["41"], "decision": "partial_marks"}, headers=HTMX_HEADERS)
    assert 'id="marking-panel"' in page.text
    assert definition_tree(draft) == expected_tree
    assert client.get(answers_url).text.count('value="cancel">') == decision_count(draft)
    with pytest.raises(GradeFlowError) as refusal:
        marked_by_example(draft, "41", "full_marks")
    refused = client.post(example, {"answer": ["41"], "decision": "full_marks"}, headers=HTMX_HEADERS)
    assert json.loads(refused.headers[MESSAGES_HEADER]) == [{"level": "error", "text": str(refusal.value)}]
    assert definition_tree(draft) == expected_tree


def test_each_answer_marked_by_example_redraws_the_editor(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    example = reverse("mark_example", args=[assessment.pk, draft.question_id])
    examples: list[tuple[str, Decision]] = [("41", "not_allowed"), ("42", "full_marks")]
    for answer, decision in examples:
        expected_tree = marked_by_example(draft, answer, decision)
        page = client.post(example, {"answer": [answer], "decision": decision}, headers=HTMX_HEADERS)
        assert definition_tree(draft) == expected_tree
        workspace = MarkingWorkspace(draft)
        assert workspace.valid
        cards = page.text.split('id="editor-notices"', 1)[1]
        steps = definition_tree(draft)["with"]["steps"]
        descriptions = [workspace.draft.describe(f"/with/steps/{index}") for index in range(len(steps))]
        assert all(markdown(description) in cards for description in descriptions)


def test_a_problem_shows_on_its_checks_card_and_on_its_form_where_no_field_holds_it(
    client: Client, assessment: Assessment
) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/workflow@1")
    light, dark = ({"uses": "gradeflow/accepted-answers@1", "with": {"answers": [text]}} for text in ("light", "dark"))

    def save_and_get_problem_message(*steps: dict[str, Any]) -> str:
        save_definition(draft, {**definition_tree(draft), "with": {"steps": list(steps)}})
        [single_problem] = MarkingWorkspace(draft).draft.problems
        return escape(single_problem.message)

    misplaced_reference_message = save_and_get_problem_message(
        {**light, "if": "{{ steps.later.output.correct }}"}, {**dark, "id": "later"}
    )
    assert misplaced_reference_message in client.get(url).text
    unknown_setting_message = save_and_get_problem_message(light, {**dark, "with": {"answers": ["dark"], "colour": 1}})
    assert f"colour: {unknown_setting_message}" in client.get(url).text
    open_response = client.post(url, {"path": "", "operation": "open", "target": "/with/steps/1"}, headers=HTMX_HEADERS)
    assert f"colour: {unknown_setting_message}" in open_response.text


def test_starting_and_discarding_a_draft_refreshes_the_users_question_list(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    published_topics: list[str] = []
    monkeypatch.setattr(marking_workspace, "notify_change", lambda *names: published_topics.extend(names))
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(reverse("draft_discard", args=[assessment.pk, draft.pk]), headers=HTMX_HEADERS)
    assert published_topics == [topic("drafts", assessment.pk, draft.user_id)] * 2


def test_changing_a_drafts_method_refreshes_the_users_question_list(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    published_topics: list[str] = []
    monkeypatch.setattr(marking_workspace, "notify_change", lambda *names: published_topics.extend(names))
    client.post(url, {"path": "", "operation": "open", "target": ""})
    assert published_topics == [], "the method is the same"
    client.post(url, {"path": "", "operation": "change_method", "target": "", "method": "gradeflow/full-marks@1"})
    assert published_topics == [topic("drafts", assessment.pk, draft.user_id)]


def test_discarding_says_what_stays(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    Marking.objects.create(
        assessment=assessment, question=question, definition=dump_yaml(accepted_answers_check("Q2", "42"))
    )
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    drafts = {"Q2": reverse("draft", args=[assessment.pk, MarkingDraftRecord.objects.get(question=question).pk])}
    drafts["Q1"], _ = start_marking(client, assessment, "Q1", "gradeflow/accepted-answers@1")
    drafts["Q3"], _ = start_marking(client, assessment, "Q3", "gradeflow/multiple-choice@1")
    stays = {"Q1": "Students keep their imported marks.", "Q2": "The saved marking stays as it was.", "Q3": ""}
    for key, said in stays.items():
        assert f'data-confirm-detail="{said}"' in client.get(drafts[key]).text


def test_discarding_a_new_question_group_returns_to_starting_one(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    discarded = client.post(
        reverse("draft_discard", args=[assessment.pk, MarkingDraftRecord.objects.get().pk]), headers=HTMX_HEADERS
    )
    assert json.loads(discarded.headers["HX-Location"])["path"] == reverse("question_group_new", args=[assessment.pk])


def test_a_removed_question_groups_answers_go_to_the_questions(client: Client, assessment: Assessment) -> None:
    marking = question_group_marking(assessment)
    client.post(reverse("question_group_remove", args=[assessment.pk, marking.pk]))
    refreshed = client.get(reverse("question_group_answers", args=[assessment.pk, marking.pk]), headers=HTMX_HEADERS)
    assert json.loads(refreshed.headers["HX-Location"])["path"] == reverse("questions", args=[assessment.pk])
    assert client.get(reverse("question_group_answers", args=[assessment.pk, marking.pk])).status_code == 404


def test_an_empty_list_of_questions_asks_for_one_instead_of_showing_the_first(
    client: Client, assessment: Assessment
) -> None:
    started = client.post(
        reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/custom-code-for-a-group@1"}
    )
    page = client.get(started.headers["Location"]).text
    assert '<option value="" disabled selected>Choose question</option>' in page


def test_choosing_group_questions_updates_the_answers_column(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/custom-code-for-a-group@1"})
    draft = MarkingDraftRecord.objects.get()
    response = client.post(
        reverse("draft", args=[assessment.pk, draft.pk]),
        {"path": "", "fields_posted": "1", "questions": ["Q1"], "code": 'results = {"Q1": {"correct": True}}'},
        headers=HTMX_HEADERS,
    )
    page = response.text
    assert 'id="answers"' in page
    assert "No questions yet" not in page


def test_code_answers_show_as_typed_and_highlighted(client: Client, assessment: Assessment) -> None:
    question = make_code_question(assessment, "Q1")
    unindented_code = "def first_positive(xs):\nfor x in xs:\nif x > 0:\nreturn x\nreturn None"
    assessment.students.update(answers={"Q1": unindented_code + "  \r\n"})
    page = client.get(reverse("answers", args=[assessment.pk, question.pk])).text
    assert '<span class="code-k">def</span>' in page
    text = html.unescape(re.sub(r"<span[^>]*>|</span>", "", page))
    assert unindented_code in text
    assert "Original" not in text


def test_a_problem_with_a_checks_settings_as_a_whole_names_only_the_check() -> None:
    questions = QuestionSet.load({"Q1": {"type": "text", "reading": {"ignore_punctuation": True}}})
    expression = {"uses": "gradeflow/equivalent-expression@1", "with": {"answers": ["x + 1"]}}
    data = {"uses": "gradeflow/workflow@1", "with": {"steps": [expression]}}
    draft = MarkingDraft(data, questions, "Q1")
    (problem,) = draft.problems
    assert problem_text(draft, problem) == f"Equivalent Expression: {problem.message}"


def test_text_answers_in_an_answer_group_are_listed_under_answers(client: Client, assessment: Assessment) -> None:
    first, second = assessment.students.order_by("pk")[:2]
    first.answers, second.answers = {"Q1": "Paris"}, {"Q1": "paris "}
    first.save()
    second.save()
    page = client.get(reverse("answers", args=[assessment.pk, assessment.questions.get(key="Q1").pk])).text
    assert '>Answers<span class="badge badge-sm badge-soft tabular-nums">2</span>' in page
    listed = between(page, 'aria-label="Answers">', "</ul>")
    assert ">Paris</p>" in listed
    assert ">paris </p>" in listed
