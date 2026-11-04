"""What the method designer shows, for its templates: the workspace, the test values and an input's settings."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from django import forms
from django.http import QueryDict
from gradeflow_core import Problem
from gradeflow_core.editing import EditingError

from gradeflow_app.editor.editing import problem_text
from gradeflow_app.editor.presenters import editor
from gradeflow_app.libraries import method_tests
from gradeflow_app.libraries.access import MethodAccess
from gradeflow_app.libraries.designer.runs import run_state, tests_topic
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.services import dependents
from gradeflow_app.requests import Request
from gradeflow_app.ui.schemaforms.forms import ObjectForm, allow_unset

WORKSPACE_TABS = ("checks", "inputs")


@dataclass(frozen=True)
class InputView:
    """An input's declaration, as the core describes it, and whether it is advanced: as the setting it fills is,
    unless it says otherwise."""

    name: str
    form: ObjectForm
    settings_filled: list[tuple[str, str]]
    advanced: bool


def state_of(request: Request, workspace: MethodWorkspace) -> dict[str, str]:
    """Where the person is in the workspace: the question shown, the check open, and the tab of the marking."""
    data = request.POST if request.method == "POST" else request.GET
    question = data.get("shown_question", "")
    tab = data.get("tab", "checks")
    return {
        "question": question if question in workspace.questions else next(iter(workspace.questions), ""),
        "path": data.get("path", ""),
        "tab": tab if tab in WORKSPACE_TABS else "checks",
        "input": data.get("input", ""),
    }


def workspace_context(
    request: Request, access: MethodAccess, workspace: MethodWorkspace, **extra: Any
) -> dict[str, Any]:
    state = state_of(request, workspace) | {
        key: extra.pop(key) for key in ("question", "path", "tab", "input") if key in extra
    }
    draft, tests_record, problems = workspace.draft, workspace.tests_record, workspace.checks.problems
    run, run_out_of_date = run_state(tests_record)
    context: dict[str, Any] = {
        "access": access,
        "member": access.member,
        "layout": access.layout,
        "workspace": workspace,
        "method": workspace.method,
        "state": state,
        "metadata": draft.metadata,
        "versions": list(workspace.method.versions.order_by("-number")),
        "has_own_draft": workspace.editable or workspace.method.drafts.filter(user=request.user).exists(),
        "selected_question": workspace.questions.get(state["question"]),
        "new_answer": method_tests.answer_form(workspace.questions.get(state["question"]), "", "answer-new"),
        "read_only": _read_only(workspace),
        "used_by": dependents(workspace.method, request.user),
        "first_problem_check": problems[0].check if problems else None,
        "first_problem_label": workspace.checks.label(problems[0].check) if problems else "",
        "first_problem_unfilled": problems[0].is_incomplete if problems else False,
        "rows": _rows(workspace, state["question"]),
        "run": run,
        "run_out_of_date": run_out_of_date,
        "urls": _urls(workspace),
        "can_edit_tests": access.can_edit or not workspace.editable,
        "topic": tests_topic(tests_record),
        "problems": _problems(workspace, problems),
        "untested": workspace.untested,
        "yaml_only_reasons": workspace.yaml_only_reasons,
        **extra,
    }
    context.setdefault("values", values_form(workspace))
    if not context["yaml_only_reasons"]:
        if state["tab"] == "inputs":
            context.setdefault("input_view", input_view(workspace, state["input"]))
            context["inputs"] = [
                (name, declaration, workspace.default_label(name))
                for name, declaration in workspace.draft.inputs.items()
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
        inputs = workspace.draft.inputs
        return [_input_problem_text(problem, inputs) for problem in workspace.draft.problems]
    return [problem_text(workspace.checks, problem) for problem in problems] if workspace.yaml_only_reasons else []


def _input_problem_text(problem: Problem, inputs: Mapping[str, Mapping[str, Any]]) -> str:
    """A method's `problem`, after the title of the input whose test value is at fault."""
    if len(problem.field) == 1 and (declaration := inputs.get(str(problem.field[0]))) is not None:
        return f"{declaration.get('title') or problem.field[0]}: {problem.message}"
    return str(problem)


def _read_only(workspace: MethodWorkspace) -> set[str]:
    """The questions the finished marking reads but gives no marks."""
    marking = workspace.finalized
    return marking.questions_read() - marking.questions_marked() if marking else set()


def _rows(workspace: MethodWorkspace, question: str) -> list[method_tests.TestAnswerView]:
    shown_question = workspace.questions.get(question)
    return method_tests.answer_rows(workspace.tests, question, workspace.last_results(), shown_question)


def _urls(workspace: MethodWorkspace) -> dict[str, str]:
    names = ["page", "answers", "values", "run", "results", "check", "inputs", "question", "details", "yaml"]
    if workspace.editable:
        names += ["publish", "discard"]
    return {name: workspace.url(f"method_{name}") for name in names}


def values_form(
    workspace: MethodWorkspace, data: QueryDict | None = None, initial_object: dict[str, Any] | None = None
) -> ObjectForm | None:
    """The form of the test values, when the method has inputs."""
    if not workspace.draft.inputs:
        return None
    try:
        schema = workspace.draft.settings_schema
    except EditingError:
        return None
    current = initial_object if initial_object is not None else workspace.defaults | workspace.draft.test_values
    form = ObjectForm(schema, current, prefix="values", data=data)
    if not workspace.editable:
        form.disable()
    return form


def input_view(
    workspace: MethodWorkspace, name: str, data: QueryDict | None = None, initial_object: dict[str, Any] | None = None
) -> InputView | None:
    declaration = workspace.draft.inputs.get(name)
    if declaration is None:
        return None
    schema = workspace.draft.input_schema(name)
    advanced = bool(schema["properties"]["advanced"]["default"])
    current = (
        {**declaration, "advanced": declaration.get("advanced", advanced)} if initial_object is None else initial_object
    )
    form = ObjectForm(schema, current, prefix="input", data=data)
    allow_unset(form, "default")
    _limit_default_to_choices(form)
    if not workspace.editable:
        form.disable()
    return InputView(name, form, workspace.settings_filled_by(name), advanced)


def _limit_default_to_choices(form: ObjectForm) -> None:
    """Offer as the default only the input's choices, as posted or saved."""
    default = form.fields.get("default")
    choices = form["choices"].value() if "choices" in form.fields else None
    if isinstance(default, forms.TypedChoiceField) and choices:
        offered = cast(list[tuple[Any, str]], default.choices)
        default.choices = [(value, label) for value, label in offered if value == "" or value in choices]
        chosen = form["default"].value()
        label = str(dict(offered).get(chosen, chosen)).replace("%", "%%")
        default.error_messages["invalid_choice"] = f"{label} isn't one of the choices."
