"""The method designer: a person's draft of a method, or a published version they try, beside their test answers.

Every view serves both: a URL with a version `number` shows that published version, one without shows the
person's draft. Each edit re-renders the workspace; an edit from a page older than the workspace changes nothing.
"""

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import EMPTY_VALUES
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from gradeflow_core import GradeFlowError, InvalidError, QuestionSet
from gradeflow_core.editing import QUESTION_TYPES, EditingError, new_question

from gradeflow_app.editor import operations
from gradeflow_app.editor.picker import PickerForm, check_picker
from gradeflow_app.libraries import method_tests
from gradeflow_app.libraries.access import MethodAccess, method_viewer
from gradeflow_app.libraries.designer import presenters
from gradeflow_app.libraries.designer.runs import run_tests, stop_tests
from gradeflow_app.libraries.designer.workspace import (
    MethodWorkspace,
    StaleWorkspace,
    example_index,
    lock_workspace,
    start_draft,
    version_tests_of,
)
from gradeflow_app.libraries.models import Method, MethodDraftRecord
from gradeflow_app.libraries.services import method_draft
from gradeflow_app.libraries.views import library_url
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.questions.forms import QuestionForm, question_type_named, retyped
from gradeflow_app.questions.models import QUESTION_TYPE_LABELS
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import is_fragment, morphed_into, redirect_page
from gradeflow_app.ui.schemaforms.forms import ObjectForm, with_row_edit


def _message(error: Exception) -> str:
    return " ".join(error.messages) if isinstance(error, ValidationError) else str(error)


def _stale(request: Request, workspace: MethodWorkspace) -> HttpResponse:
    return render(request, "methods/changed_elsewhere.html", {"url": workspace.url("method_page")}, status=409)


def _workspace(request: Request, access: MethodAccess, number: int | None) -> MethodWorkspace:
    method = access.method
    if number is not None:
        return MethodWorkspace(
            method, version_tests_of(get_object_or_404(method.versions, number=number), request.user)
        )
    draft_record = MethodDraftRecord.objects.select_related("based_on").filter(method=method, user=request.user).first()
    if draft_record is None:
        raise Http404
    return MethodWorkspace(method, draft_record)


def _render_workspace(request: Request, access: MethodAccess, workspace: MethodWorkspace, **extra: Any) -> HttpResponse:
    return render(request, "methods/work.html", presenters.workspace_context(request, access, workspace, **extra))


def _edit(
    request: Request,
    access: MethodAccess,
    number: int | None,
    change: Callable[[MethodWorkspace], dict[str, Any] | None],
) -> HttpResponse:
    """Apply `change` to the workspace and commit it, then show it with any run it started; or show why it
    couldn't."""
    workspace = _workspace(request, access, number)
    if workspace.editable and not access.can_edit:
        raise PermissionDenied
    try:
        with transaction.atomic():
            current = lock_workspace(workspace, request.POST.get("revision"))
            extra = change(current) or {}
    except StaleWorkspace:
        return _stale(request, workspace)
    except GradeFlowError as error:
        return _render_workspace(request, access, workspace, notice=_message(error))
    return _render_workspace(request, access, MethodWorkspace(current.method, current.tests_record), **extra)


def _draft_only(workspace: MethodWorkspace) -> None:
    if not workspace.editable:
        raise Http404


@method_viewer()
def page(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    if number is None and not access.method.drafts.filter(user=request.user).exists():
        return redirect("method_latest", access.method.pk)
    workspace = _workspace(request, access, number)
    template = "methods/work.html" if is_fragment(request) else "methods/page.html"
    return render(request, template, presenters.workspace_context(request, access, workspace))


@method_viewer()
def latest_version(request: Request, access: MethodAccess) -> HttpResponse:
    """A method's latest version."""
    method = access.method
    latest = method.latest_version
    if latest is not None:
        return redirect("method_page", method.pk, latest.number)
    if method.drafts.filter(user=request.user).exists():
        return redirect("method_page", method.pk)
    raise Http404


@require_POST
@method_viewer()
@transaction.atomic
def answers(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """Change the test answers: add, write, clear or delete one, mark it as an example, or reset them all."""

    def change(workspace: MethodWorkspace) -> None:
        tests, data = workspace.tests, request.POST
        question = presenters.state_of(request, workspace)["question"]
        row = int(row_text) if (row_text := data.get("row", "")).isdigit() else -1
        shown = workspace.questions.get(question)
        match data.get("operation"):
            case "add":
                method_tests.add_answer(tests, question, method_tests.posted_answer(shown, data))
            case "save":
                method_tests.set_answer(tests, row, question, method_tests.posted_answer(shown, data))
            case "clear":
                method_tests.set_answer(tests, row, question, "")
            case "delete":
                method_tests.delete_row(tests, row)
            case "example":
                _draft_only(workspace)
                method_tests.toggle_example(tests, row)
            case "reset" if not workspace.editable:
                assert workspace.version is not None
                workspace.tests_record.save_revision(deepcopy(workspace.version.tests))
                return
            case _:
                raise Http404
        workspace.save()

    return _edit(request, access, number, change)


@require_POST
@method_viewer()
@transaction.atomic
def values(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """Change the values the inputs have in a draft's test runs, or go back to their defaults. A published
    version's are those it was published with."""

    def change(workspace: MethodWorkspace) -> dict[str, Any] | None:
        _draft_only(workspace)
        operation = request.POST.get("operation", "")
        if operation == "reset":
            workspace.draft.test_values = {}
            workspace.save()
            return {"values_expanded": True}
        form = presenters.values_form(workspace, request.POST)
        if form is None:
            return None
        form.full_clean()
        current = form.values()
        if (edited := with_row_edit(form, current, request.POST)) is not None:
            return {"values": presenters.values_form(workspace, initial_object=edited), "values_expanded": True}
        if not form.is_valid():
            return {"values": form, "values_expanded": True}
        defaults = workspace.defaults
        workspace.draft.test_values = {name: value for name, value in current.items() if value != defaults.get(name)}
        workspace.save()
        return {"values_expanded": True}

    return _edit(request, access, number, change)


@require_POST
@method_viewer()
def run(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    def change(workspace: MethodWorkspace) -> None:
        run_tests(workspace.tests_record, endpoint(request) if workspace.test_run_asks_models() else None)

    return _edit(request, access, number, change)


@method_viewer()
def results(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """The answers column and, in a draft, its Publish button, refreshed as a test run finishes."""
    workspace = _workspace(request, access, number)
    return render(
        request, "methods/work.html#answers", presenters.workspace_context(request, access, workspace, out_of_band=True)
    )


@require_POST
@method_viewer()
@transaction.atomic
def check(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """Open a check of the marking; in a draft, also change it as the editor's form says. The whole workspace
    answers, even the form's own edits, so the page always holds the revision it shows."""
    workspace = _workspace(request, access, number)
    if not workspace.editable:
        if request.POST.get("operation") != "open":
            raise Http404
        response = _render_workspace(request, access, workspace, path=operations.apply(workspace, request.POST, ""))
    else:

        def change(current: MethodWorkspace) -> dict[str, Any]:
            if current.yaml_only_reasons:
                raise GradeFlowError("Edit this method as YAML.")
            path = operations.apply(current, request.POST, presenters.state_of(request, current)["path"])
            current.save()
            return {"path": path}

        response = _edit(request, access, number, change)
    return morphed_into(response, "#method-work") if response.status_code == 200 else response


@method_viewer(edit=True)
def method_choices(request: Request, access: MethodAccess) -> HttpResponse:
    """The method picker for the check at `target` of the draft."""
    workspace = _workspace(request, access, None)
    picker_form = PickerForm(
        action=workspace.url("method_check"),
        target="#method-work",
        swap="outerMorph",
        include="#method-state",
    )
    return check_picker(
        request, workspace.checks, workspace.offers, workspace.course, picker_form, workspace.url("method_choices")
    )


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def fill_setting(request: Request, access: MethodAccess) -> HttpResponse:
    """Fill a setting with a fixed value, a new input, or an existing one."""

    def change(workspace: MethodWorkspace) -> dict[str, Any]:
        field_path = request.POST.get("field", "")
        field = next((each for each in workspace.draft.configurable_fields() if each.path == field_path), None)
        if field is None:
            raise Http404
        match request.POST.get("operation"):
            case "unbind":
                workspace.draft.unbind(field_path)
            case "new_input":
                workspace.draft.bind_new_input(field_path)
            case "bind":
                workspace.draft.bind(field_path, request.POST.get("input", ""))
            case _:
                raise Http404
        workspace.save()
        return {"path": field.check}

    return _edit(request, access, None, change)


@method_viewer()
@transaction.atomic
def inputs(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """The inputs tab, or one input's settings; posting changes or removes the input."""
    if request.method != "POST":
        workspace = _workspace(request, access, number)
        return _render_workspace(request, access, workspace, tab="inputs")

    def change(workspace: MethodWorkspace) -> dict[str, Any] | None:
        _draft_only(workspace)
        name = request.POST.get("input", "")
        if request.POST.get("operation") == "remove":
            workspace.draft.remove_input(name)
            workspace.save()
            return {"tab": "inputs", "input": ""}
        input_view = presenters.input_view(workspace, name, request.POST)
        if input_view is None:
            raise GradeFlowError("This input is no longer there.")
        form = input_view.form
        form.full_clean()
        values = form.values()
        if (edited := with_row_edit(form, values, request.POST)) is not None:
            return {
                "tab": "inputs",
                "input": name,
                "input_view": presenters.input_view(workspace, name, initial_object=edited),
            }
        if not form.is_valid():
            return {"tab": "inputs", "input": name, "input_view": input_view}
        declaration = {key: value for key, value in values.items() if value not in EMPTY_VALUES}
        if declaration.get("advanced") == input_view.advanced:
            del declaration["advanced"]
        try:
            workspace.draft.update_input(name, declaration)
        except InvalidError as error:
            form.add_problems(error.problems)
            return {"tab": "inputs", "input": name, "input_view": input_view}
        except EditingError as error:
            form.add_problem((), str(error))
            return {"tab": "inputs", "input": name, "input_view": input_view}
        workspace.save()
        return {"tab": "inputs", "input": ""}

    return _edit(request, access, number, change)


@method_viewer()
@transaction.atomic
def question(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """A dialog with a question's settings; posting adds, changes, duplicates or removes it."""
    workspace = _workspace(request, access, number)
    data = request.POST if request.method == "POST" else request.GET
    key = data.get("key", "")
    existing = workspace.questions.get(key)
    # A new question is like the last one, as parts (a), (b) and (c) usually are.
    type_source = existing or next(reversed(list(workspace.questions.values())), None)
    default_type = next(iter(QUESTION_TYPES))
    question_type = question_type_named(
        data.get("type", type_source.type if type_source else default_type), default_type
    )
    answers = method_tests.answers_to(workspace.tests, key)
    question = retyped(existing, question_type, answers) if existing else new_question(question_type)
    operation = "save" if "save" in request.POST else request.POST.get("operation", "")
    saving = operation == "save"
    form = QuestionForm(question, data=request.POST if saving else None)
    if not workspace.editable:
        form.disable()
    edited_question = form.posted_question() if saving else None
    if workspace.editable and (operation in ("duplicate", "remove") or edited_question is not None):

        def change(current: MethodWorkspace) -> dict[str, Any]:
            draft, tests = current.draft, current.tests  # the draft as it was, for set_questions to compare
            if key and key not in tests.get("questions", {}):
                raise GradeFlowError("This question is no longer there.")
            if operation == "remove":
                method_tests.remove_question(tests, key)
                shown_key = next(iter(tests.get("questions", {})), "")
            elif operation == "duplicate":
                shown_key = method_tests.duplicate_question(tests, key)
            else:
                assert edited_question is not None
                shown_key = method_tests.set_question(tests, key, edited_question)
            draft.set_questions(QuestionSet.model_validate(tests["questions"]))
            current.save()
            return {"question": shown_key}

        return _edit(request, access, number, change)
    context = {
        "action": workspace.url("method_question"),
        "workspace": workspace,
        "key": key,
        "question_type": question_type,
        "types": QUESTION_TYPE_LABELS.items(),
        "form": form,
    }
    return render(request, "methods/question.html", context, status=422 if saving else 200)


@method_viewer()
@transaction.atomic
def details(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """A dialog with the method's name, summary, guide and how it marks; posting changes them."""
    workspace = _workspace(request, access, number)
    draft = workspace.draft
    form = ObjectForm(draft.metadata_schema, draft.metadata, data=request.POST if request.method == "POST" else None)
    if not workspace.editable:
        form.disable()
    elif request.method == "POST" and form.is_valid():
        values = form.values()

        def change(current: MethodWorkspace) -> None:
            current.draft.update_metadata(**values)
            current.save()

        return _edit(request, access, number, change)
    context = {"action": workspace.url("method_details"), "workspace": workspace, "form": form}
    return render(request, "methods/details.html", context, status=422 if form.is_bound else 200)


@method_viewer()
@transaction.atomic
def yaml(request: Request, access: MethodAccess, number: int | None = None) -> HttpResponse:
    """The method as YAML; posting a draft's replaces it once the core can read it as a method."""
    workspace = _workspace(request, access, number)
    text, problem = workspace.definition, ""
    if request.method == "POST" and workspace.editable:
        text = request.POST.get("definition", "")
        try:
            draft = method_draft(workspace.method, text, workspace.tests, workspace.methods)
        except (EditingError, InvalidError) as error:
            problem = str(error)
        else:

            def change(current: MethodWorkspace) -> None:
                assert current.draft_record is not None
                current.draft_record.definition = draft.to_yaml()
                tests = current.tests
                tests["example_index"] = example_index(draft, tests)
                current.tests_record.save_revision(tests)

            return _edit(request, access, number, change)
    context = {"action": workspace.url("method_yaml"), "workspace": workspace, "text": text, "problem": problem}
    return render(request, "methods/yaml.html", context, status=422 if problem else 200)


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def start_editing(request: Request, access: MethodAccess) -> HttpResponse:
    """Start the person's draft of a method from the version they were looking at."""
    method = access.method
    number = request.POST.get("number", "")
    versions = method.versions.order_by("-number")
    version = (versions.filter(number=number) if number.isdigit() else versions).first()
    if version is None:
        raise Http404
    start_draft(method, request.user, version)
    url = reverse("method_page", args=[method.pk])
    return redirect_page(request, url)


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def publish_draft(request: Request, access: MethodAccess) -> HttpResponse:
    workspace = _workspace(request, access, None)
    try:
        Method.objects.select_for_update().get(pk=workspace.method.pk)
        version = lock_workspace(workspace, request.POST.get("revision")).publish(request.user)
    except StaleWorkspace:
        return _stale(request, workspace)
    except (GradeFlowError, ValidationError) as error:
        return _render_workspace(request, access, workspace, notice=_message(error))
    messages.success(request, f"Published version {version.number}.")
    url = reverse("method_page", args=[access.method.pk, version.number])
    return redirect_page(request, url)


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def discard(request: Request, access: MethodAccess) -> HttpResponse:
    workspace = _workspace(request, access, None)
    method = workspace.method
    stop_tests(workspace.tests_record)
    workspace.tests_record.delete()
    if method.versions.exists():
        url = reverse("method_latest", args=[method.pk])
    else:
        if not method.drafts.exists():
            method.delete()
        url = library_url(method.library)
    messages.success(request, "Draft discarded.")
    return redirect_page(request, url)
