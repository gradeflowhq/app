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
from gradeflow_app.editor.picker import PickerUse, check_picker
from gradeflow_app.jobs.queue import stop
from gradeflow_app.libraries import workbench
from gradeflow_app.libraries.access import Viewer, method_viewer
from gradeflow_app.libraries.designer import presenters
from gradeflow_app.libraries.designer.runs import run_tests
from gradeflow_app.libraries.designer.workspace import (
    MethodWorkspace,
    Stale,
    example_at,
    locked,
    start_draft,
    version_tests_of,
)
from gradeflow_app.libraries.models import Method, MethodDraft
from gradeflow_app.libraries.services import draft_of, publish
from gradeflow_app.libraries.views import library_url
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.questions.forms import QuestionForm, chosen_type, retyped
from gradeflow_app.questions.models import TYPE_LABELS
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import go, is_fragment, morph
from gradeflow_app.ui.schemaforms.forms import ObjectForm, rows_edited


def _message(error: Exception) -> str:
    return " ".join(error.messages) if isinstance(error, ValidationError) else str(error)


def _stale(request: Request, workspace: MethodWorkspace) -> HttpResponse:
    return render(request, "methods/stale.html", {"url": workspace.url("method_page")}, status=409)


def _workspace(request: Request, viewer: Viewer, number: int | None) -> MethodWorkspace:
    found = viewer.method
    if number is not None:
        return MethodWorkspace(found, version_tests_of(get_object_or_404(found.versions, number=number), request.user))
    draft = MethodDraft.objects.select_related("based_on", "run").filter(method=found, user=request.user).first()
    if draft is None:
        raise Http404
    return MethodWorkspace(found, draft)


def _work(request: Request, viewer: Viewer, workspace: MethodWorkspace, **extra: Any) -> HttpResponse:
    return render(request, "methods/work.html", presenters.context(request, viewer, workspace, **extra))


def _edit(
    request: Request,
    viewer: Viewer,
    number: int | None,
    change: Callable[[MethodWorkspace], dict[str, Any] | None],
) -> HttpResponse:
    """Apply `change` to the workspace, then show it; or show why it couldn't."""
    workspace = _workspace(request, viewer, number)
    if workspace.editable and not viewer.can_edit:
        raise PermissionDenied
    try:
        current = locked(workspace, request.POST.get("revision"))
        extra = change(current) or {}
    except Stale:
        return _stale(request, workspace)
    except GradeFlowError as error:
        return _work(request, viewer, workspace, notice=_message(error))
    return _work(request, viewer, MethodWorkspace(current.method, current.session), **extra)


def _draft_only(workspace: MethodWorkspace) -> None:
    if not workspace.editable:
        raise Http404


@method_viewer()
def page(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    if number is None and not viewer.method.drafts.filter(user=request.user).exists():
        return redirect("published", viewer.method.pk)
    workspace = _workspace(request, viewer, number)
    template = "methods/work.html" if is_fragment(request) else "methods/page.html"
    return render(request, template, presenters.context(request, viewer, workspace))


@method_viewer()
def published(request: Request, viewer: Viewer) -> HttpResponse:
    """A method's latest version."""
    found = viewer.method
    latest = found.latest_version
    if latest is not None:
        return redirect("method_page", found.pk, latest.number)
    if found.drafts.filter(user=request.user).exists():
        return redirect("method_page", found.pk)
    raise Http404


@require_POST
@method_viewer()
@transaction.atomic
def answers(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """Change the test answers: add, write, clear or delete one, mark it as an example, or reset them all."""

    def change(workspace: MethodWorkspace) -> None:
        tests, data = workspace.tests, request.POST
        question = presenters.state_of(request, workspace)["question"]
        row = int(given) if (given := data.get("row", "")).isdigit() else -1
        shown = workspace.questions.get(question)
        match data.get("op"):
            case "add":
                workbench.add_answer(tests, question, workbench.posted_answer(shown, data))
            case "save":
                workbench.set_answer(tests, row, question, workbench.posted_answer(shown, data))
            case "clear":
                workbench.set_answer(tests, row, question, "")
            case "delete":
                workbench.delete_row(tests, row)
            case "example":
                _draft_only(workspace)
                workbench.toggle_example(tests, row)
            case "reset" if not workspace.editable:
                assert workspace.version is not None
                workspace.session.revise(deepcopy(workspace.version.tests))
                return
            case _:
                raise Http404
        workspace.save()

    return _edit(request, viewer, number, change)


@require_POST
@method_viewer()
@transaction.atomic
def values(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """Change the values the inputs have in a draft's test runs, or go back to their defaults. A published
    version's are those it was published with."""

    def change(workspace: MethodWorkspace) -> dict[str, Any] | None:
        _draft_only(workspace)
        op = request.POST.get("op", "")
        if op == "reset":
            workspace.core.test_values = {}
            workspace.save()
            return {"values_open": True}
        form = presenters.values_form(workspace, request.POST)
        if form is None:
            return None
        form.full_clean()
        current = form.values()
        if (edited := rows_edited(form, current, request.POST)) is not None:
            return {"values": presenters.values_form(workspace, value=edited), "values_open": True}
        if not form.is_valid():
            return {"values": form, "values_open": True}
        defaults = workspace.defaults
        workspace.core.test_values = {name: value for name, value in current.items() if value != defaults.get(name)}
        workspace.save()
        return {"values_open": True}

    return _edit(request, viewer, number, change)


@require_POST
@method_viewer()
@transaction.atomic
def run(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    def change(workspace: MethodWorkspace) -> None:
        workspace.session.run = run_tests(
            workspace.session, workspace.test_run(endpoint(request)), workspace.course, request.user
        )

    return _edit(request, viewer, number, change)


@method_viewer()
def results(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """The answers column and, in a draft, its Publish button, refreshed as a test run finishes."""
    workspace = _workspace(request, viewer, number)
    return render(request, "methods/work.html#answers", presenters.context(request, viewer, workspace, oob=True))


@require_POST
@method_viewer()
@transaction.atomic
def check(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """Open a check of the marking; in a draft, also change it as the editor's form says. The whole workspace
    answers, even the form's own edits, so the page always holds the revision it shows."""
    workspace = _workspace(request, viewer, number)
    if not workspace.editable:
        if request.POST.get("op") != "open":
            raise Http404
        response = _work(request, viewer, workspace, path=operations.apply(workspace, request.POST, ""))
    else:

        def change(current: MethodWorkspace) -> dict[str, Any]:
            if current.uneditable:
                raise GradeFlowError("Edit this method as YAML.")
            path = operations.apply(current, request.POST, presenters.state_of(request, current)["path"])
            current.save()
            return {"path": path}

        response = _edit(request, viewer, number, change)
    return morph(response, "#method-work") if response.status_code == 200 else response


@method_viewer(edit=True)
def methods_to_choose(request: Request, viewer: Viewer) -> HttpResponse:
    """The method picker for the check at `target` of the draft."""
    workspace = _workspace(request, viewer, None)
    use = PickerUse(
        action=workspace.url("method_check"),
        field="value",
        target="#method-work",
        swap="outerMorph",
        include="#method-state",
    )
    return check_picker(
        request, workspace.checks, workspace.offers, workspace.course, use, workspace.url("method_picker")
    )


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def field(request: Request, viewer: Viewer) -> HttpResponse:
    """Fill a setting with a fixed value, a new input, or an existing one."""

    def change(workspace: MethodWorkspace) -> dict[str, Any]:
        path = request.POST.get("field", "")
        found = next((field for field in workspace.core.fields() if field.path == path), None)
        if found is None:
            raise Http404
        match request.POST.get("op"):
            case "fix":
                workspace.core.fix(path)
            case "new":
                workspace.core.expose(path)
            case "bind":
                workspace.core.bind(path, request.POST.get("input", ""))
            case _:
                raise Http404
        workspace.save()
        return {"path": found.check}

    return _edit(request, viewer, None, change)


@method_viewer()
@transaction.atomic
def inputs(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """The inputs tab, or one input's settings; posting changes or removes the input."""
    if request.method != "POST":
        workspace = _workspace(request, viewer, number)
        return _work(request, viewer, workspace, tab="inputs")

    def change(workspace: MethodWorkspace) -> dict[str, Any] | None:
        _draft_only(workspace)
        name = request.POST.get("input", "")
        if request.POST.get("op") == "remove":
            workspace.core.remove_input(name)
            workspace.save()
            return {"tab": "inputs", "input": ""}
        found = presenters.input_view(workspace, name, request.POST)
        if found is None:
            raise GradeFlowError("This input is no longer there.")
        form = found.form
        form.full_clean()
        values = form.values()
        if (edited := rows_edited(form, values, request.POST)) is not None:
            return {"tab": "inputs", "input": name, "input_view": presenters.input_view(workspace, name, value=edited)}
        if not form.is_valid():
            return {"tab": "inputs", "input": name, "input_view": found}
        declared = {key: value for key, value in values.items() if value not in EMPTY_VALUES}
        if declared.get("advanced") == found.advanced:
            del declared["advanced"]
        try:
            workspace.core.update_input(name, declared)
        except InvalidError as error:
            form.add_problems(error.problems)
            return {"tab": "inputs", "input": name, "input_view": found}
        except EditingError as error:
            form.add_problem((), str(error))
            return {"tab": "inputs", "input": name, "input_view": found}
        workspace.save()
        return {"tab": "inputs", "input": ""}

    return _edit(request, viewer, None, change)


@method_viewer()
@transaction.atomic
def question(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """A dialog with a question's settings; posting adds, changes, duplicates or removes it."""
    workspace = _workspace(request, viewer, number)
    data = request.POST if request.method == "POST" else request.GET
    key = data.get("key", "")
    old = workspace.questions.get(key)
    # A new question is like the last one, as parts (a), (b) and (c) usually are.
    like = old or next(reversed(list(workspace.questions.values())), None)
    first = next(iter(QUESTION_TYPES))
    chosen = chosen_type(data.get("type", like.type if like else first), first)
    value = retyped(old, chosen, workbench.answers_to(workspace.tests, key)) if old else new_question(chosen)
    saving = request.method == "POST" and "save" in request.POST
    form = QuestionForm(value, data=request.POST if saving else None)
    if not workspace.editable:
        form.disable()
    op = request.POST.get("op", "save" if saving else "")
    changed = form.question() if saving and op == "save" else None
    if request.method == "POST" and workspace.editable and (op in ("duplicate", "remove") or changed is not None):

        def change(current: MethodWorkspace) -> dict[str, Any]:
            core, tests = current.core, current.tests  # the draft as it was, for set_questions to compare
            if key and key not in tests.get("questions", {}):
                raise GradeFlowError("This question is no longer there.")
            if op == "remove":
                workbench.remove_question(tests, key)
                shown = next(iter(tests.get("questions", {})), "")
            elif op == "duplicate":
                shown = workbench.duplicate_question(tests, key)
            else:
                assert changed is not None
                shown = workbench.set_question(tests, key, changed)
            core.set_questions(QuestionSet.model_validate(tests["questions"]))
            current.save()
            return {"question": shown}

        return _edit(request, viewer, number, change)
    context = {
        "action": workspace.url("method_question"),
        "workspace": workspace,
        "key": key,
        "chosen": chosen,
        "types": TYPE_LABELS.items(),
        "form": form,
    }
    return render(request, "methods/question.html", context, status=422 if saving else 200)


@method_viewer()
@transaction.atomic
def details(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """A dialog with the method's name, summary, guide and how it marks; posting changes them."""
    workspace = _workspace(request, viewer, number)
    core = workspace.core
    schema = core.metadata_schema
    form = ObjectForm(schema, core.metadata, data=request.POST if request.method == "POST" else None)
    if not workspace.editable:
        form.disable()
    elif request.method == "POST" and form.is_valid():
        values = form.values()

        def change(current: MethodWorkspace) -> None:
            current.core.update_metadata(**values)
            current.save()

        return _edit(request, viewer, number, change)
    context = {"action": workspace.url("method_details"), "workspace": workspace, "form": form}
    return render(request, "methods/details.html", context, status=422 if form.is_bound else 200)


@method_viewer()
@transaction.atomic
def yaml(request: Request, viewer: Viewer, number: int | None = None) -> HttpResponse:
    """The method as YAML; posting a draft's replaces it once the core can read it as a method."""
    workspace = _workspace(request, viewer, number)
    text, problem = workspace.definition, ""
    if request.method == "POST" and workspace.editable:
        text = request.POST.get("definition", "")
        try:
            read = draft_of(workspace.method, text, workspace.tests, workspace.methods)
        except (EditingError, InvalidError) as error:
            problem = str(error)
        else:

            def change(current: MethodWorkspace) -> None:
                assert current.draft is not None
                current.draft.definition = read.to_yaml()
                tests = current.tests
                tests["example_at"] = example_at(read, tests)
                current.session.revise(tests)

            return _edit(request, viewer, number, change)
    context = {"action": workspace.url("method_yaml"), "workspace": workspace, "text": text, "problem": problem}
    return render(request, "methods/yaml.html", context, status=422 if problem else 200)


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def edit(request: Request, viewer: Viewer) -> HttpResponse:
    """Start the person's draft of a method from the version they were looking at."""
    found = viewer.method
    number = request.POST.get("number", "")
    versions = found.versions.order_by("-number")
    version = (versions.filter(number=number) if number.isdigit() else versions).first()
    if version is None:
        raise Http404
    start_draft(found, request.user, version)
    url = reverse("method_page", args=[found.pk])
    return go(request, url)


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def publish_draft(request: Request, viewer: Viewer) -> HttpResponse:
    workspace = _workspace(request, viewer, None)
    try:
        Method.objects.select_for_update().get(pk=workspace.method.pk)
        current = locked(workspace, request.POST.get("revision"))
        assert current.draft is not None
        if not current.changed:
            assert current.version is not None
            raise ValidationError(f"Nothing changed since version {current.version.number}.")
        if not current.valid:
            raise ValidationError(f"Fix the marking first: {current.problems[0].message}")
        if current.untested:
            raise ValidationError("Run the tests first, so the examples show their marks.")
        # One step: a publish that fails keeps the draft's examples and test run as they were.
        with transaction.atomic():
            current.record_examples()
            run = current.session.run
            tested = run if current.results() is not None else None
            version = publish(current.draft, request.user, current.core)
            if tested is None:
                stop(run)
            else:
                # The run marked what was just published, so the version starts with its results.
                tests = version_tests_of(version, request.user)
                tests.run, tests.revision = tested, current.session.revision
                tests.save(update_fields=["run", "revision"])
    except Stale:
        return _stale(request, workspace)
    except (GradeFlowError, ValidationError) as error:
        return _work(request, viewer, workspace, notice=_message(error))
    messages.success(request, f"Published version {version.number}.")
    url = reverse("method_page", args=[viewer.method.pk, version.number])
    return go(request, url)


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def discard(request: Request, viewer: Viewer) -> HttpResponse:
    workspace = _workspace(request, viewer, None)
    found = workspace.method
    stop(workspace.session.run)
    workspace.session.delete()
    if found.versions.exists():
        url = reverse("published", args=[found.pk])
    else:
        if not found.drafts.exists():
            found.delete()
        url = library_url(found.library)
    messages.success(request, "Draft discarded.")
    return go(request, url)
