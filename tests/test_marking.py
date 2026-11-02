import html
import json
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from django.utils.html import escape
from gradeflow_core import Check, GradeFlowError, InvalidError, Methods, Question, QuestionSet
from gradeflow_core.editing import DECISIONS, Decision, MarkingDraft, answer_decisions, dump_yaml, mark_by_example
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.editor.editing import located
from gradeflow_app.editor.presenters import editor
from gradeflow_app.imports import models as imports
from gradeflow_app.jobs.models import Job
from gradeflow_app.libraries.collection import in_use
from gradeflow_app.live.topics import topic
from gradeflow_app.marking import checks, views
from gradeflow_app.marking.answers import answer_groups, column
from gradeflow_app.marking.checks import tree_of
from gradeflow_app.marking.models import Draft, Marking
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.ui.templatetags.ui import markdown
from tests.conftest import HTMX, code_draft, envelope, start_marking

pytestmark = pytest.mark.django_db


def tree(draft: Draft) -> dict[str, Any]:
    draft.refresh_from_db()
    return tree_of(draft.definition)


def store(draft: Draft, definition: dict[str, Any]) -> None:
    draft.definition = dump_yaml(definition)
    draft.save()


def accepted(question: str, answer: str) -> dict[str, Any]:
    return {"question": question, "uses": "gradeflow/accepted-answers@1", "with": {"answers": [answer]}}


def said_of(draft: Draft, part: str | int) -> str:
    """What the core says is wrong with the draft's field whose location holds `part`."""
    draft.refresh_from_db()
    return escape(next(problem.message for problem in Workspace(draft).checks.problems if part in problem.field))


def group_marking(assessment: Assessment) -> Marking:
    conditional = {
        "uses": "gradeflow/conditional@1",
        "with": {"condition": [accepted("Q1", "light")], "then": [accepted("Q2", "42")]},
    }
    return Marking.objects.create(assessment=assessment, definition=dump_yaml(conditional))


def workspace_of(assessment: Assessment, definition: dict[str, Any], key: str | None = "Q1") -> Workspace:
    question = assessment.questions.get(key=key) if key else None
    user = assessment.course.members.get().user
    return Workspace(Draft(assessment=assessment, question=question, user=user, definition=dump_yaml(definition)))


def test_a_typing_post_reads_the_answers_once(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    read: list[str] = []
    answer_to = imports.answer_to

    def reading(key: str) -> Any:
        read.append(key)
        return answer_to(key)

    monkeypatch.setattr("gradeflow_app.marking.workspace.answer_to", reading)
    response = client.post(url, {"path": "", "fields": "1", "points": ["light"], "combine": "all"})
    assert response.status_code == 200
    assert read == ["Q1"]


def test_stale_or_unknown_editor_actions_change_nothing(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    client.post(url, {"path": "", "op": "add", "target": "with.checks.0", "value": "gradeflow/key-points@1"})
    client.post(url, {"path": "", "op": "remove", "target": "with.checks.0"})
    before = tree(draft)
    stale = [
        {"path": "", "op": "remove", "target": "with.checks.0"},
        {"path": "", "op": "move", "target": "with.checks.3", "value": "down"},
        {"path": "", "op": "add", "target": "with.nowhere.0", "value": "gradeflow/key-points@1"},
        {"path": "", "op": "open", "target": "with.checks.7"},
        {"path": "with.checks.4", "fields": "1", "points": ["light"]},
        {"path": "", "op": "retype", "target": "", "value": "gradeflow/nope@1"},
        {"path": "", "op": "add", "target": "with.checks.0", "value": "gradeflow/nope@1"},
    ]
    for data in stale:
        assert client.post(url, data).status_code == 200, data
    assert tree(draft) == before
    assert client.get(url, {"path": "with.setup"}).status_code == 200


def test_questions_opens_the_first_question(client: Client, assessment: Assessment) -> None:
    """Marked or not. A page load goes to its address; a click in the app gets it at once, with its address."""
    Marking.objects.create(
        assessment=assessment,
        question=assessment.questions.get(key="Q1"),
        definition=dump_yaml({"question": "Q1", "uses": "gradeflow/full-marks@1"}),
    )
    url = reverse("questions", args=[assessment.pk])
    first = reverse("question", args=[assessment.pk, assessment.questions.get(key="Q1").pk])
    assert client.get(url).headers["Location"] == first
    clicked = client.get(url, headers=HTMX)
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
    client.post(url, {"path": "", "fields": "1", "points": ["light"], "combine": "all"})
    question = draft.question
    assert question is not None
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    history = reverse("marking_history", args=[assessment.pk, question.pk])
    assert "Current" in client.get(history).content.decode()
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("marking_remove", args=[assessment.pk, question.pk]))
    assert not Marking.objects.exists()
    versions = client.get(history).content.decode()
    assert "Current" not in versions
    assert "Restore" in versions
    assert history in client.get(reverse("question", args=[assessment.pk, question.pk])).content.decode()


def test_restoring_a_version_drops_the_preview_of_the_draft_it_replaces(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields": "1", "answers": ["42"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    question = assessment.questions.get(key="Q2")
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    draft = Draft.objects.get()
    client.post(reverse("draft", args=[assessment.pk, draft.pk]), {"path": "", "fields": "1", "answers": ["41"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    answers = reverse("answers", args=[assessment.pk, question.pk])
    previewed = '<option value="changed"'
    assert previewed in client.get(answers).content.decode()
    version = Marking.history.get().history_id
    client.post(reverse("marking_restore", args=[assessment.pk, question.pk, version]))
    assert previewed not in client.get(answers).content.decode()


def test_while_previewing_the_marks_filters_follow_the_previewed_marks(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields": "1", "answers": ["42"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    question = assessment.questions.get(key="Q2")
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    draft = Draft.objects.get()
    client.post(reverse("draft", args=[assessment.pk, draft.pk]), {"path": "", "fields": "1", "answers": ["41"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    answers = reverse("answers", args=[assessment.pk, question.pk])
    full = client.get(answers, {"filter": "full", "group": "answer"}, headers=HTMX).content.decode()
    assert "1 student<" in full, "41, given full marks by the preview"
    assert "5 students" not in full, "42, full marks only in the saved marking"
    zero = client.get(answers, {"filter": "zero", "group": "answer"}, headers=HTMX).content.decode()
    assert "5 students" in zero


def test_questions_cache_validation_and_markings_use_the_supplied_collection(
    client: Client, assessment: Assessment
) -> None:
    question = assessment.questions.get(key="Q2")
    assert question.core is question.core
    question.definition = {**question.definition, "max_marks": 3}
    assert question.core.max_marks == 3
    definition = dump_yaml(accepted("Q2", "42"))
    methods = in_use(assessment.course)
    checked = checks.check_of(definition, methods)
    assert checked is not None
    assert checked.dump() == accepted("Q2", "42")
    without = Methods(method for method in methods if method.ref != "gradeflow/accepted-answers@1")
    assert checks.check_of(definition, without) is None


def test_a_new_question_group_asks_for_what_it_needs(client: Client, assessment: Assessment) -> None:
    started = client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    page = client.get(started.headers["Location"])
    assert page.status_code == 200
    assert b"None yet" in page.content
    assert b"Required." not in page.content, "an empty new list isn't a mistake yet"
    draft = Draft.objects.get()
    saving = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    saved = saving.content.decode()
    assert 'role="alert"' not in saved
    assert "Add at least one check." in saved.split('id="slot-1"', 1)[1].split("</section>", 1)[0], "on the empty list"


def test_a_problem_with_the_assessment_shows_on_the_field_at_fault(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q3", "gradeflow/multiple-choice@1")
    store(draft, {"question": "Q3", "uses": "gradeflow/multiple-choice@1", "with": {"correct": ["a", "e"]}})
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).content.decode()
    errors = page.split('id="id_correct_error"', 1)[1].split("</div>", 1)[0]
    assert said_of(draft, "correct") in errors


def parameter_rows(url: str, client: Client, fields: dict[str, str], rows: list[tuple[str, str, str]]) -> str:
    posted = {"parameters-present": "1"}
    for index, (key, tag, value) in enumerate(rows):
        posted |= {f"parameters-{index}-key": key, f"parameters-{index}-tag": tag, f"parameters-{index}-value": value}
    return client.post(url, fields | posted).content.decode()


def test_parameters_are_named_and_checked_as_the_core_says(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    client.post(url, fields | {"op": "add_row", "target": "parameters"})
    page = client.post(url, fields | {"op": "add_row", "target": "parameters"}).content.decode()
    draft = Draft.objects.get()
    assert list(tree(draft)["with"]["parameters"]) == ["parameter_1", "parameter_2"]
    assert "Add parameter" in page
    assert 'pattern="^[A-Za-z_][A-Za-z0-9_]*$"' in page
    rows = [("1st", "integer", "2"), ("parameter_2", "integer", "0.5")]
    page = parameter_rows(url, client, fields | {"fields": "1"}, rows)
    first = page.split('id="parameters-0-error"', 1)[1].split("</div>", 1)[0]
    assert said_of(draft, "1st") in first
    assert said_of(draft, "parameter_2") in page.split('id="parameters-1-error"', 1)[1].split("</div>", 1)[0]
    assert tree(draft)["with"]["parameters"]["parameter_2"]["value"] == "0.5", "kept as typed, for the core to check"


def test_a_new_parameter_never_replaces_another(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    for _ in range(2):
        client.post(url, fields | {"op": "add_row", "target": "parameters"})
    client.post(url, fields | {"op": "remove_row", "target": "parameters", "value": "parameter_1"})
    client.post(url, fields | {"op": "add_row", "target": "parameters"})
    assert len(tree(Draft.objects.get())["with"]["parameters"]) == 2


def test_parameters_the_editor_cant_edit_are_kept_as_they_are(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    draft = Draft.objects.get()
    listed = {"type": "list", "value": [{"type": "integer", "value": 1}]}
    definition = tree(draft)
    definition["with"]["parameters"] = {"sizes": listed, "n": {"type": "integer", "value": 1}}
    store(draft, definition)
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).content.decode()
    assert "Can't be edited here" in page
    posted = {"parameters-present": "1", "parameters-0-key": "sizes", "parameters-1-key": "n"}
    posted |= {"parameters-1-tag": "integer", "parameters-1-value": "2"}
    client.post(url, fields | {"fields": "1"} | posted)
    assert tree(draft)["with"]["parameters"] == {"sizes": listed, "n": {"type": "integer", "value": 2}}


def test_a_parameter_is_removed_by_the_name_its_row_has_now(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    draft = Draft.objects.get()
    definition = tree(draft)
    definition["with"]["parameters"] = {"n": {"type": "integer", "value": 1}}
    store(draft, definition)
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).content.decode()
    (vals,) = re.findall(r'hx-vals="([^"]*remove_row[^"]*)"', page)
    row = {
        "parameters-present": "1",
        "parameters-0-key": 'a"b',
        "parameters-0-tag": "integer",
        "parameters-0-value": "1",
    }
    client.post(url, fields | row | json.loads(html.unescape(vals)))
    assert "parameters" not in tree(draft)["with"], "none left, as by default"


def test_a_saved_marking_the_core_now_rejects_opens_and_says_so(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    _, fields = code_draft(client, assessment)
    client.post(reverse("draft_save", args=[assessment.pk, Draft.objects.get().pk]), fields | {"op": "save"})
    marking = Marking.objects.get()
    definition = tree_of(marking.definition)
    definition["with"]["parameters"] = {"answer": {"type": "integer", "value": 1}}
    marking.definition = dump_yaml(definition)
    marking.save()
    question = marking.question
    assert question is not None
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).content.decode()
    assert "Marks nothing until fixed" in page
    settings = reverse("question_settings", args=[assessment.pk, question.pk])
    with django_capture_on_commit_callbacks(execute=True):
        saved = client.post(settings, {"question_type": "text", "save": "1", "max_marks": "2"})
    assert saved.status_code == 200, "marking the question again skips what can't mark"
    started = client.post(reverse("marking_start", args=[assessment.pk, question.pk])).content.decode()
    assert said_of(Draft.objects.get(), "answer") in started
    assert Marking.objects.filter(pk=marking.pk).exists(), "kept until it's fixed"


def test_crumbs_name_each_check_as_its_card_does(assessment: Assessment) -> None:
    points = {"name": "Light", "uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}
    combination = {
        "question": "Q1",
        "uses": "gradeflow/combination@1",
        "with": {"checks": [points, {"uses": "gradeflow/not-allowed@1", "with": {}}]},
    }
    workspace = workspace_of(assessment, combination)
    for path in ("with.checks.0", "with.checks.1"):
        crumbs = editor(workspace, path).crumbs
        assert crumbs == [(at, workspace.checks.label(at)) for at in ("", path)]


def test_a_check_inside_a_groups_check_marks_that_checks_question(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    draft = Draft.objects.get()
    url = reverse("draft", args=[assessment.pk, draft.pk])
    picker = client.get(
        reverse("draft_methods", args=[assessment.pk, draft.pk]), {"op": "add", "target": "with.then.0"}
    )
    assert 'name="question"' in picker.content.decode(), "its methods depend on the question"
    added = {"path": "", "op": "add", "target": "with.then.0", "value": "gradeflow/combination@1", "question": "Q3"}
    assert client.post(url, added).status_code == 200
    inner = {
        "path": "with.then.0",
        "op": "add",
        "target": "with.then.0.with.checks.0",
        "value": "gradeflow/multiple-choice@1",
    }
    client.post(url, inner)
    opened = client.post(url, {"path": "", "op": "open", "target": "with.then.0.with.checks.0"})
    assert opened.status_code == 200
    assert 'name="correct"' in opened.content.decode(), "Q3's options to choose from"


def test_a_question_in_a_group_names_the_others(client: Client, assessment: Assessment) -> None:
    group_marking(assessment)
    question = assessment.questions.get(key="Q2")
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).content.decode()
    assert "Question group Q1, Q2" in page


def test_checks_are_moved_and_removed(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    for at, method in enumerate(("gradeflow/key-points@1", "gradeflow/accepted-answers@1")):
        client.post(url, {"path": "", "op": "add", "target": f"with.checks.{at}", "value": method})
    client.post(url, {"path": "", "op": "move", "target": "with.checks.0", "value": "down"})
    assert [check["uses"] for check in tree(draft)["with"]["checks"]] == [
        "gradeflow/accepted-answers@1",
        "gradeflow/key-points@1",
    ]
    page = client.post(url, {"path": "with.checks.1", "op": "remove", "target": "with.checks.1"}).content.decode()
    assert [check["uses"] for check in tree(draft)["with"]["checks"]] == ["gradeflow/accepted-answers@1"]
    assert 'name="path" value=""' in page, "removing the check shown goes back to what holds it"


def code_question(assessment: Assessment, key: str) -> None:
    """Question `key` as code, which Code Tests marks."""
    question = assessment.questions.get(key=key)
    question.core = Question.load({"type": "code", "max_marks": question.core.max_marks})
    question.save()


def test_rows_are_added_and_removed(client: Client, assessment: Assessment) -> None:
    code_question(assessment, "Q1")
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/code-tests@1")
    for _ in range(2):
        client.post(url, {"path": "", "op": "add_row", "target": "tests"})
    assert tree(draft)["with"]["tests"] == [{}, {}]
    rows = {"tests-present": "1"}
    for index, expression in enumerate(("add(1, 2)", "add(2, 2)")):
        rows |= {f"tests-{index}-expression": expression, f"tests-{index}-expected": "3"}
    client.post(url, {"path": "", "fields": "1", "op": "remove_row", "target": "tests", "value": "0"} | rows)
    assert tree(draft)["with"]["tests"] == [{"expression": "add(2, 2)", "expected": "3"}]


def test_problems_with_rows_show_on_the_row_at_fault(client: Client, assessment: Assessment) -> None:
    code_question(assessment, "Q1")
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/code-tests@1")
    client.post(url, {"path": "", "op": "add_row", "target": "tests"})
    saving = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    errors = saving.content.decode().split('id="id_tests-0-expression_error"', 1)[1].split("</div>", 1)[0]
    assert "text-error" in errors


def test_a_cleared_text_field_is_cleared(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/pattern@1")
    for typed in ("a+", ""):
        client.post(url, {"path": "", "fields": "1", "pattern": typed})
        assert tree(draft)["with"]["pattern"] == typed


def test_numbers_the_core_rejects_say_why_on_their_field(client: Client, assessment: Assessment) -> None:
    code_question(assessment, "Q1")
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/code-tests@1")
    for typed in ("0", "soon"):
        page = client.post(url, {"path": "", "fields": "1", "setup__time_limit": typed}).content.decode()
        errors = page.split('id="id_setup__time_limit_error"', 1)[1].split("</div>", 1)[0]
        assert "text-error" in errors, typed
        assert tree(draft)["with"]["setup"]["time_limit"] == (0 if typed == "0" else typed), "kept as typed"


def answers_of(client: Client, url: str) -> str:
    return client.get(url, headers=HTMX).content.decode()


def test_a_question_groups_answers_show_by_combination(client: Client, assessment: Assessment) -> None:
    marking = group_marking(assessment)
    saved = answers_of(client, reverse("question_group_answers", args=[assessment.pk, marking.pk]))
    assert ">Q2</h2>" in saved
    assert "Read only" in saved, "the condition's question"
    assert 'class="preview-bar"' not in saved
    client.post(reverse("question_group_edit", args=[assessment.pk, marking.pk]))
    editing = answers_of(client, reverse("question_group_answers", args=[assessment.pk, marking.pk]))
    assert 'class="preview-bar"' not in editing, "nothing to preview until the draft changes"
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    new = Draft.objects.get(marking=None)
    fresh = answers_of(client, reverse("question_group_draft_answers", args=[assessment.pk, new.pk]))
    assert "No questions yet" in fresh
    assert reverse("question_group_draft_answers", args=[assessment.pk, new.pk]) in fresh


def test_restoring_a_version_opens_it_as_a_draft(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields": "1", "answers": ["light"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    question = assessment.questions.get(key="Q1")
    version = Marking.history.get().history_id
    restored = client.post(reverse("marking_restore", args=[assessment.pk, question.pk, version]), headers=HTMX)
    assert json.loads(restored.headers["HX-Location"])["path"] == reverse("question", args=[assessment.pk, question.pk])
    assert tree(Draft.objects.get(question=question))["with"]["answers"] == ["light"]


def test_a_draft_says_when_someone_saved_the_marking_since_it_started(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields": "1", "answers": ["light"]})
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    question = assessment.questions.get(key="Q1")
    page = reverse("question", args=[assessment.pk, question.pk])
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    notice = "Someone saved this marking after you started."
    assert notice not in client.get(page).content.decode()
    Marking.objects.get().save()
    assert notice in client.get(page).content.decode()


def highlighted(client: Client, page: str) -> list[str]:
    """What the question list, refreshed in place on `page`, shows as current, by link."""
    rendered = client.get(page).content.decode()
    refresh = re.search(r'<nav id="question-list"[^>]*hx-get="([^"]*)"', rendered)
    assert refresh is not None
    listed = client.get(html.unescape(refresh.group(1)), headers=HTMX).content.decode()
    return re.findall(r'href="([^"]*)" class="list-link[^"]*" aria-current="page"', listed)


def test_the_question_list_highlights_what_the_page_shows(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    marking = group_marking(assessment)
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    draft = Draft.objects.get()
    for page in (
        reverse("question", args=[assessment.pk, question.pk]),
        reverse("question_group", args=[assessment.pk, marking.pk]),
        reverse("question_group_draft", args=[assessment.pk, draft.pk]),
        reverse("question_group_new", args=[assessment.pk]),
    ):
        assert highlighted(client, page) == [page]


def test_the_question_list_validates_each_marking_once(client: Client, assessment: Assessment) -> None:
    group_marking(assessment)
    from unittest.mock import patch

    with patch.object(Check, "load", wraps=Check.load) as validate:
        client.get(reverse("question_list", args=[assessment.pk]), headers=HTMX)
    assert validate.call_count == 1


def test_marking_an_answer_redraws_the_editor_and_the_answers(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    url = reverse("mark_example", args=[assessment.pk, draft.question_id])
    page = client.post(url, {"given": "full", "answer": "42"}, headers=HTMX).content.decode()
    assert 'id="marking-panel"' in page
    assert 'id="answers"' in page.split('id="marking-panel"', 1)[0]
    assert 'hx-swap-oob="outerMorph"' in page


def test_the_way_back_opens_what_holds_the_check(client: Client, assessment: Assessment) -> None:
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    client.post(url, {"path": "", "op": "add", "target": "with.checks.0", "value": "gradeflow/key-points@1"})
    page = client.post(url, {"path": "with.checks.0", "op": "open", "target": ""}).content.decode()
    assert 'name="path" value=""' in page


def test_too_many_answers_to_compare_are_grouped_when_equal(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(answer_groups, "MAX_COMPARED", 2)
    url = reverse("answers", args=[assessment.pk, assessment.questions.get(key="Q1").pk])
    for grouping in ("spelling", "meaning"):
        page = client.get(url, {"group": grouping}, headers=HTMX).content.decode()
        assert "Too many answers to compare. Showing equal answers only." in page, grouping
    assert not Job.objects.filter(kind=Job.Kind.GROUPING).exists(), "nothing to compare"
    assert "Too many answers" not in client.get(url, {"group": "answers"}, headers=HTMX).content.decode()


def test_more_answer_groups_show_on_request_until_the_list_changes(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(column, "SHOWN", 4)
    url = reverse("answers", args=[assessment.pk, assessment.questions.get(key="Q1").pk])

    def shown(**query: str) -> tuple[int, bool]:
        page = client.get(url, query, headers=HTMX).content.decode()
        return page.count('<details class="group/answer'), "Show more" in page

    assert shown() == (4, True)
    assert shown(shown="8") == (8, True)
    assert shown() == (8, True)
    assert shown(shown="12") == (10, False)
    assert shown(filter="all") == (4, True)


def test_a_values_check_suggests_that_values_answers(assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    question.core = Question.load(
        {"type": "values", "value_types": ["text", "text"], "max_marks": question.core.max_marks}
    )
    question.save()
    assessment.students.filter(key=envelope("S001")).update(answers={"Q1": "light, water"})
    check = {"uses": "gradeflow/accepted-answers@1", "with": {"answers": []}}
    values = {"question": "Q1", "uses": "gradeflow/multiple-values@1", "with": {"checks": [check, check]}}
    workspace = workspace_of(assessment, values)
    assert workspace.suggestions_at(workspace.checks.position("with.checks.1")) == {"water": 1}


def test_the_marking_opens_from_its_crumb_whose_path_is_empty(client: Client, assessment: Assessment) -> None:
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    client.post(url, {"path": "", "op": "add", "target": "with.checks.0", "value": "gradeflow/key-points@1"})
    page = client.post(url, {"path": "with.checks.0", "op": "open"})
    assert page.status_code == 200
    assert 'name="path" value=""' in page.content.decode()


def test_choosing_another_question_in_the_picker_keeps_where_the_check_goes(
    client: Client, assessment: Assessment
) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    draft = Draft.objects.get()
    methods = reverse("draft_methods", args=[assessment.pk, draft.pk])
    picker = client.get(methods, {"op": "add", "target": "with.condition.0", "path": ""}).content.decode()
    reload = html.unescape(re.findall(r'hx-get="([^"]*)" hx-include="this"', picker)[0])
    again = client.get(f"{reload}&question=Q3").content.decode()
    assert '<input type="hidden" name="target" value="with.condition.0">' in again
    assert '<input type="hidden" name="path" value="">' in again
    assert 'data-method="gradeflow/multiple-choice@1"' in again, "Q3's methods"


def test_a_question_groups_check_may_be_a_method_of_several_questions(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/combination@1"})
    draft = Draft.objects.get()
    methods = reverse("draft_methods", args=[assessment.pk, draft.pk])
    first = client.get(methods, {"op": "add", "target": "with.checks.0", "path": ""}).content.decode()
    assert '<option value="">Several questions</option>' in first
    assert 'data-method="gradeflow/answer-sets@1"' not in first, "the first question's methods"
    several = client.get(methods, {"op": "add", "target": "with.checks.0", "path": "", "question": ""}).content.decode()
    assert '<option value="" selected>Several questions</option>' in several
    assert 'data-method="gradeflow/answer-sets@1"' in several
    added = {"path": "", "op": "add", "target": "with.checks.0", "value": "gradeflow/answer-sets@1", "question": ""}
    client.post(reverse("draft", args=[assessment.pk, draft.pk]), added)
    draft.refresh_from_db()
    assert "question" not in draft.definition.split("answer-sets", 1)[1]


def test_a_question_has_one_marking_but_a_draft_marking_it_again_previews(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    Marking.objects.create(
        assessment=assessment, question=assessment.questions.get(key="Q2"), definition=dump_yaml(accepted("Q2", "42"))
    )
    marking = group_marking(assessment)
    client.post(reverse("question_group_edit", args=[assessment.pk, marking.pk]))
    draft = Draft.objects.get()
    draft.definition = dump_yaml({**tree_of(draft.definition), "name": "Edited group"})
    draft.save(update_fields=["definition"])
    saving = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    assert "Q2 has another marking. A question has one marking." in saving.content.decode()
    assert (
        f'href="{reverse("question", args=[assessment.pk, assessment.questions.get(key="Q2").pk])}"'
        in saving.content.decode()
    )
    assert Draft.objects.filter(pk=draft.pk).exists()
    assert "Edited group" not in Marking.objects.get(pk=marking.pk).definition
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    draft.refresh_from_db()
    assert draft.preview is not None, "a preview saves nothing, so it needn't wait for the other marking"


def test_group_validation_refreshes_when_another_marking_releases_its_question(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    question = assessment.questions.get(key="Q2")
    Marking.objects.create(assessment=assessment, question=question, definition=dump_yaml(accepted("Q2", "42")))
    marking = group_marking(assessment)
    client.post(reverse("question_group_edit", args=[assessment.pk, marking.pk]))
    draft = Draft.objects.get()
    draft.definition = dump_yaml({**tree_of(draft.definition), "name": "Edited group"})
    draft.save(update_fields=["definition"])
    page = client.get(reverse("question_group", args=[assessment.pk, marking.pk])).content.decode()
    status_url = reverse("draft", args=[assessment.pk, draft.pk]) + "?status=1"
    assert status_url in page
    assert 'hx-include="#marking-form [name=path]"' in page
    before = client.get(status_url).content.decode()
    assert "Q2 has another marking" in before
    original = draft.definition
    client.post(reverse("marking_remove", args=[assessment.pk, question.pk]))
    after = client.get(status_url).content.decode()
    assert "Q2 has another marking" not in after
    assert 'id="preview-bar"' in after
    assert 'id="marking-form"' not in after
    draft.refresh_from_db()
    assert draft.definition == original
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_preview", args=[assessment.pk, draft.pk]))
    previewed = client.get(status_url).content.decode()
    assert "students&#x27; results change" in previewed or "students' results change" in previewed
    assert 'id="marking-form"' not in previewed
    client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    assert client.get(status_url).status_code == 204
    assert client.get(reverse("draft", args=[assessment.pk, draft.pk])).status_code == 404


def test_a_draft_is_edited_as_yaml_with_problems_on_their_lines(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    assert "Edit as YAML" in client.get(url).content.decode()
    sheet = reverse("draft_yaml", args=[assessment.pk, draft.pk])
    assert "uses: gradeflow/key-points@1" in client.get(sheet).content.decode()
    workspace = Workspace(draft)

    def shows_what_the_core_says(response: Any, text: str) -> None:
        try:
            said = [
                str(p)
                for p in MarkingDraft.from_yaml(text, workspace.questions, "Q1", methods=workspace.methods).problems
            ]
        except InvalidError as error:
            said = [str(p) for p in error.problems]
        assert said
        assert all(escape(problem) in response.content.decode() for problem in said)

    typed = "question: Q1\nuses: gradeflow/key-points@1\nwith:\n  points: []\n"
    wrong = client.post(sheet, {"definition": typed}, headers=HTMX)
    assert wrong.status_code == 422
    shows_what_the_core_says(wrong, typed)
    assert tree(draft)["with"] == {"points": []}, "kept as typed, to fix in either editor"
    assert "Saved to your draft" in wrong.content.decode()
    assert 'id="marking-panel" class="flex min-h-full flex-col" hx-swap-oob' in wrong.content.decode()
    unread = client.post(sheet, {"definition": "uses: [\n"}, headers=HTMX)
    shows_what_the_core_says(unread, "uses: [\n")
    assert "Not saved" in unread.content.decode()
    assert "marking-panel" not in unread.content.decode()
    unnamed = "uses: gradeflow/key-points@1\nwith:\n  points: [light]\n"
    fixed = client.post(sheet, {"definition": unnamed}, headers=HTMX)
    assert fixed.headers["HX-Refresh"] == "true"
    assert tree(draft) == {"question": "Q1", "uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}


def by_example(draft: Draft, answer: str, given: Decision) -> dict[str, Any]:
    """The draft as the core marks `answer` by example, written as the draft is saved."""
    draft.refresh_from_db()
    workspace = Workspace(draft)
    question = workspace.subject.question
    assert question is not None
    marked = mark_by_example(workspace.checks.data, [answer], given, methods=workspace.methods, question=question.core)
    return Check.load(marked, methods=workspace.methods).dump()


def decided(draft: Draft) -> int:
    """How many answers the core says the draft decided, each of which the answers column offers to clear."""
    draft.refresh_from_db()
    workspace = Workspace(draft)
    return len(answer_decisions(workspace.checks.data, methods=workspace.methods))


def test_an_answer_group_is_marked_by_example(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(url, {"path": "", "fields": "1", "answers": ["42"]})
    question = assessment.questions.get(key="Q2")
    answers_url = reverse("answers", args=[assessment.pk, question.pk])
    answers = client.get(answers_url).content.decode()
    assert all(f'value="{given}">{label}</button>' in answers for given, label in DECISIONS.items())
    assert answers.count('value="cancel">') == decided(draft)
    example = reverse("mark_example", args=[assessment.pk, question.pk])
    marked = by_example(draft, "41", "part")
    page = client.post(example, {"answer": ["41"], "given": "part"}, headers=HTMX)
    assert 'id="marking-panel"' in page.content.decode()
    assert tree(draft) == marked
    assert client.get(answers_url).content.decode().count('value="cancel">') == decided(draft)
    with pytest.raises(GradeFlowError) as refusal:
        by_example(draft, "41", "full")
    refused = client.post(example, {"answer": ["41"], "given": "full"}, headers=HTMX)
    assert json.loads(refused.headers["X-Messages"]) == [{"level": "error", "text": str(refusal.value)}]
    assert tree(draft) == marked


def test_each_answer_marked_by_example_redraws_the_editor(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    example = reverse("mark_example", args=[assessment.pk, draft.question_id])
    examples: list[tuple[str, Decision]] = [("41", "not_allowed"), ("42", "full")]
    for answer, given in examples:
        marked = by_example(draft, answer, given)
        page = client.post(example, {"answer": [answer], "given": given}, headers=HTMX)
        assert tree(draft) == marked
        workspace = Workspace(draft)
        assert workspace.valid
        cards = page.content.decode().split('id="editor-live"', 1)[1]
        checks = tree(draft)["with"]["checks"]
        described = [workspace.checks.describe(f"with.checks.{index}") for index in range(len(checks))]
        assert all(markdown(description) in cards for description in described)


def test_a_problem_shows_on_its_checks_card_and_on_its_form_where_no_field_holds_it(
    client: Client, assessment: Assessment
) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    light, dark = ({"uses": "gradeflow/accepted-answers@1", "with": {"answers": [text]}} for text in ("light", "dark"))

    def problem(*checks: dict[str, Any]) -> str:
        store(draft, {**tree(draft), "with": {"checks": list(checks)}})
        [found] = Workspace(draft).checks.problems
        return escape(found.message)

    misplaced = problem({**light, "if": "{{ checks.later.correct }}"}, {**dark, "id": "later"})
    assert misplaced in client.get(url).content.decode()
    unknown = problem(light, {**dark, "with": {"answers": ["dark"], "colour": 1}})
    assert f"colour: {unknown}" in client.get(url).content.decode()
    opened = client.post(url, {"path": "", "op": "open", "target": "with.checks.1"}, headers=HTMX)
    assert f"colour: {unknown}" in opened.content.decode()


def test_starting_and_discarding_a_draft_refreshes_the_users_question_list(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    heard: list[str] = []
    monkeypatch.setattr(views, "publish", lambda *names: heard.extend(names))
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(reverse("draft_discard", args=[assessment.pk, draft.pk]), headers=HTMX)
    assert heard == [topic("drafts", assessment.pk, draft.user_id)] * 2


def test_changing_a_drafts_method_refreshes_the_users_question_list(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    heard: list[str] = []
    monkeypatch.setattr(views, "publish", lambda *names: heard.extend(names))
    client.post(url, {"path": "", "op": "open", "target": ""})
    assert heard == [], "the method is the same"
    client.post(url, {"path": "", "op": "retype", "target": "", "value": "gradeflow/full-marks@1"})
    assert heard == [topic("drafts", assessment.pk, draft.user_id)]


def test_discarding_says_what_stays(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    Marking.objects.create(assessment=assessment, question=question, definition=dump_yaml(accepted("Q2", "42")))
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]))
    drafts = {"Q2": reverse("draft", args=[assessment.pk, Draft.objects.get(question=question).pk])}
    drafts["Q1"], _ = start_marking(client, assessment, "Q1", "gradeflow/accepted-answers@1")
    drafts["Q3"], _ = start_marking(client, assessment, "Q3", "gradeflow/multiple-choice@1")
    stays = {"Q1": "Students keep their imported marks.", "Q2": "The saved marking stays as it was.", "Q3": ""}
    for key, said in stays.items():
        assert f'data-confirm-detail="{said}"' in client.get(drafts[key]).content.decode()


def test_discarding_a_new_question_group_returns_to_starting_one(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    discarded = client.post(reverse("draft_discard", args=[assessment.pk, Draft.objects.get().pk]), headers=HTMX)
    assert json.loads(discarded.headers["HX-Location"])["path"] == reverse("question_group_new", args=[assessment.pk])


def test_a_removed_question_groups_answers_go_to_the_questions(client: Client, assessment: Assessment) -> None:
    marking = group_marking(assessment)
    client.post(reverse("question_group_remove", args=[assessment.pk, marking.pk]))
    refreshed = client.get(reverse("question_group_answers", args=[assessment.pk, marking.pk]), headers=HTMX)
    assert json.loads(refreshed.headers["HX-Location"])["path"] == reverse("questions", args=[assessment.pk])
    assert client.get(reverse("question_group_answers", args=[assessment.pk, marking.pk])).status_code == 404


def test_an_empty_list_of_questions_asks_for_one_instead_of_showing_the_first(
    client: Client, assessment: Assessment
) -> None:
    started = client.post(
        reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/custom-code-for-a-group@1"}
    )
    page = client.get(started.headers["Location"]).content.decode()
    assert '<option value="" disabled selected>Choose question</option>' in page


def test_choosing_group_questions_updates_the_answers_column(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/custom-code-for-a-group@1"})
    draft = Draft.objects.get()
    response = client.post(
        reverse("draft", args=[assessment.pk, draft.pk]),
        {"path": "", "fields": "1", "questions": ["Q1"], "code": 'results = {"Q1": {"correct": True}}'},
        headers=HTMX,
    )
    page = response.content.decode()
    assert 'id="answers"' in page
    assert "No questions yet" not in page


def test_code_answers_show_their_likeliest_layout_and_as_exported(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    question.core = Question.load(
        {"type": "code", "reading": {"guess_indentation": True}, "max_marks": question.core.max_marks}
    )
    question.save()
    flat = "def first_positive(xs):\nfor x in xs:\nif x > 0:\nreturn x\nreturn None"
    assessment.students.update(answers={"Q1": flat})
    page = client.get(reverse("answers", args=[assessment.pk, question.pk])).content.decode()
    assert escape("def first_positive(xs):\n    for x in xs:\n        if x > 0:") in page
    assert "font-mono" in page
    assert "As exported" in page
    assert escape(flat) in page


def test_a_problem_with_a_checks_settings_as_a_whole_names_only_the_check() -> None:
    questions = QuestionSet.load({"Q1": {"type": "text", "max_marks": 1}})
    length = {"uses": "gradeflow/length@1", "with": {"min_length": 30, "max_length": 20}}
    data = {"uses": "gradeflow/combination@1", "with": {"checks": [length]}}
    draft = MarkingDraft(data, questions, "Q1")
    (problem,) = draft.problems
    assert located(draft, problem) == f"Length: {problem.message}"
