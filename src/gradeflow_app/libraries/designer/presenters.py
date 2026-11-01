"""What the method designer shows, for its templates: the workspace, the test values and an input's settings."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from django import forms
from django.http import QueryDict
from gradeflow_core import Problem
from gradeflow_core.editing import EditingError

from gradeflow_app.editor.presenters import editor
from gradeflow_app.libraries import workbench
from gradeflow_app.libraries.access import Viewer
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.services import dependents
from gradeflow_app.requests import Request
from gradeflow_app.ui.schemaforms.forms import ObjectForm, allow_unset

TABS = ("checks", "inputs")


@dataclass(frozen=True)
class InputView:
    """An input's declaration, as the core describes it, and whether it is advanced: as the setting it fills is,
    unless it says otherwise."""

    name: str
    form: ObjectForm
    used: list[tuple[str, str]]
    advanced: bool


def state_of(request: Request, workspace: MethodWorkspace) -> dict[str, str]:
    """Where the person is in the workspace: the question shown, the check open, and the tab of the marking."""
    data = request.POST if request.method == "POST" else request.GET
    shown = data.get("shown", "")
    tab = data.get("tab", "checks")
    return {
        "question": shown if shown in workspace.questions else next(iter(workspace.questions), ""),
        "path": data.get("path", ""),
        "tab": tab if tab in TABS else "checks",
        "input": data.get("input", ""),
    }


def context(request: Request, viewer: Viewer, workspace: MethodWorkspace, **extra: Any) -> dict[str, Any]:
    state = state_of(request, workspace) | {
        key: extra.pop(key) for key in ("question", "path", "tab", "input") if key in extra
    }
    core, session, problems = workspace.core, workspace.session, workspace.problems
    job = session.run
    context: dict[str, Any] = {
        "viewer": viewer,
        "member": viewer.member,
        "layout": viewer.layout,
        "workspace": workspace,
        "method": workspace.method,
        "state": state,
        "details": core.metadata,
        "versions": list(workspace.method.versions.order_by("-number")),
        "own_draft": workspace.editable or workspace.method.drafts.filter(user=request.user).exists(),
        "selected": workspace.questions.get(state["question"]),
        "new_answer": workbench.answer_form(workspace.questions.get(state["question"]), "", "answer-new"),
        "read_only": _read_only(workspace),
        "used": dependents(workspace.method, request.user),
        "unfinished": problems[0].check if problems else None,
        "unfinished_label": workspace.label(problems[0].check) if problems else "",
        "unfinished_incomplete": problems[0].incomplete if problems else False,
        "rows": _rows(workspace, state["question"]),
        "run": job,
        "run_stale": job is not None and job.revision != session.revision,
        "urls": _urls(workspace),
        "can_change": viewer.can_edit or not workspace.editable,
        "topic": "method-draft" if workspace.editable else "version-tests",
        "problems": _problems(workspace, problems),
        "untested": workspace.untested,
        "uneditable": workspace.uneditable,
        **extra,
    }
    context.setdefault("values", values_form(workspace))
    if not context["uneditable"]:
        if state["tab"] == "inputs":
            context.setdefault("input_view", input_view(workspace, state["input"]))
            context["inputs"] = [
                (name, spec, workspace.shown_default(name)) for name, spec in workspace.core.inputs.items()
            ]
        else:
            # Opened to finish it, a check shows what it still needs, as saving a question's marking does.
            finishing = (request.POST if request.method == "POST" else request.GET).get("finish") == "1"
            context["editor"] = editor(workspace, state["path"], saving=finishing)
            context["finishing"] = finishing
    return context


def _problems(workspace: MethodWorkspace, problems: list[Problem]) -> list[str]:
    """The problems listed above the method: the marking's own `problems`, while there is no editor to show them in
    place, and then the method's, once the marking has none."""
    if not problems:
        inputs = workspace.core.inputs
        return [_said(problem, inputs) for problem in workspace.core.problems]
    return [workspace.said(problem) for problem in problems] if workspace.uneditable else []


def _said(problem: Problem, inputs: Mapping[str, Mapping[str, Any]]) -> str:
    """A method's `problem`, after the title of the input whose test value is at fault."""
    if len(problem.field) == 1 and (spec := inputs.get(str(problem.field[0]))) is not None:
        return f"{spec.get('title') or problem.field[0]}: {problem.message}"
    return str(problem)


def _read_only(workspace: MethodWorkspace) -> set[str]:
    """The questions the finished marking reads but gives no marks."""
    marking = workspace.finished
    return marking.questions_read() - marking.questions_marked() if marking else set()


def _rows(workspace: MethodWorkspace, question: str) -> list[workbench.Listed]:
    shown = workspace.questions.get(question)
    return workbench.listed(workspace.tests, question, workspace.last_results(), shown)


def _urls(workspace: MethodWorkspace) -> dict[str, str]:
    names = ["page", "answers", "values", "run", "results", "check", "inputs", "question", "details", "yaml"]
    if workspace.editable:
        names += ["picker", "field", "publish", "discard"]
    return {name: workspace.url(f"method_{name}") for name in names}


def values_form(
    workspace: MethodWorkspace, data: QueryDict | None = None, value: dict[str, Any] | None = None
) -> ObjectForm | None:
    """The form of the test values, when the method has inputs."""
    if not workspace.core.inputs:
        return None
    try:
        schema = workspace.core.settings_schema
    except EditingError:
        return None
    current = value if value is not None else workspace.defaults | workspace.core.test_values
    form = ObjectForm(schema, current, prefix="values", data=data)
    if not workspace.editable:
        form.disable()
    return form


def input_view(
    workspace: MethodWorkspace, name: str, data: QueryDict | None = None, value: dict[str, Any] | None = None
) -> InputView | None:
    spec = workspace.core.inputs.get(name)
    if spec is None:
        return None
    schema = workspace.core.input_schema(name)
    advanced = bool(schema["properties"]["advanced"]["default"])
    current = {**spec, "advanced": spec.get("advanced", advanced)} if value is None else value
    form = ObjectForm(schema, current, prefix="input", data=data)
    allow_unset(form, "default")
    _offer_kept(form)
    if not workspace.editable:
        form.disable()
    return InputView(name, form, workspace.uses(name), advanced)


def _offer_kept(form: ObjectForm) -> None:
    """Offer as the default only the choices the input keeps, as posted or saved."""
    default = form.fields.get("default")
    kept = form["choices"].value() if "choices" in form.fields else None
    if isinstance(default, forms.TypedChoiceField) and kept:
        offered = cast(list[tuple[Any, str]], default.choices)
        default.choices = [(value, label) for value, label in offered if value == "" or value in kept]
        chosen = form["default"].value()
        label = str(dict(offered).get(chosen, chosen)).replace("%", "%%")
        default.error_messages["invalid_choice"] = f"{label} isn't one of the choices."
