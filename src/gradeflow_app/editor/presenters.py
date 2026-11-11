"""The step editor, for its templates: the step at a path as a form, with cards for the steps it holds."""

from collections.abc import Sequence
from dataclasses import dataclass, field

from django.utils.text import get_text_list
from gradeflow_core import GradeFlowError, Methods, Problem
from gradeflow_core import Method as CoreMethod
from gradeflow_core.editing import MarkingDraft, StepsSetting

from gradeflow_app.editor.editing import Conflict, EditorActions, StepWorkspace, problem_text
from gradeflow_app.editor.guide import choices, question_choice
from gradeflow_app.libraries.collection import BUILTIN_NAMES, OfferedMethods
from gradeflow_app.ui.schemaforms.forms import ObjectForm


@dataclass(frozen=True)
class StepCard:
    """A step in a setting, as a card that opens it."""

    path: str
    label: str
    description: str
    has_problems: bool
    problem: str
    """What is wrong with it or a step it holds, rather than not filled in yet."""
    notes: list[str]
    """What its place says about it: the question it reads, its weight, its condition."""


@dataclass(frozen=True)
class StepsSettingView:
    setting: StepsSetting
    errors: list[str]
    children: list[StepCard]
    questions: list[str]
    """In a question group, the questions an added step may read, chosen before its method."""
    can_add: bool
    unavailable_reason: str = ""
    """Why steps can't be added yet, as the core says."""
    label_controls: str = ""
    needs_steps: bool = False
    """Whether it still needs steps."""
    add_prompt: str = ""

    @property
    def has_fixed_count(self) -> bool:
        """A list of a fixed number of steps: none can be added or removed."""
        return self.setting.is_list and self.setting.minimum == self.setting.maximum


def _add_prompt(setting: StepsSetting) -> str:
    if not setting.is_list:
        return f"Add a {setting.item_label}."
    if setting.minimum > 1:
        return f"Add at least {setting.minimum} {setting.item_label}s."
    return f"Add at least one {setting.item_label}."


def _made_by(method: CoreMethod | None, offers: OfferedMethods) -> str:
    if method is None or method.name in BUILTIN_NAMES:
        return ""
    owner = offers.owners.get(method.name)
    return f"by {owner} · Version {method.version}" if owner else f"Version {method.version}"


def _problem_elsewhere_text(workspace: StepWorkspace, problem: Problem, open_path: str) -> str:
    """A problem of a step other than the one at `open_path`; one not filled in yet, as what to do."""
    label = workspace.marking_draft.label(problem.step_path) if problem.step_path else "The marking"
    return f"Finish {label}." if problem.is_incomplete else problem_text(workspace.marking_draft, problem, open_path)


def _card(workspace: StepWorkspace, path: str) -> StepCard:
    """The step at `path` as a card, with the first problem of it or a step it holds."""
    marking_draft = workspace.marking_draft
    problems = [
        each for each in marking_draft.problems if each.step_path == path or each.step_path.startswith(f"{path}/")
    ]
    problem = next((each for each in problems if not each.is_incomplete), None)
    return StepCard(
        path=path,
        label=marking_draft.label(path),
        description=marking_draft.describe(path),
        has_problems=bool(problems),
        problem=problem_text(marking_draft, problem, path) if problem else "",
        notes=marking_draft.notes(path),
    )


def _steps_setting_view(
    workspace: StepWorkspace, setting: StepsSetting, errors: list[str], label_controls: str, needs_steps: bool
) -> StepsSettingView:
    marking_draft = workspace.marking_draft
    children = [_card(workspace, child) for child in setting.step_paths]
    methods, unavailable_reason, questions = Methods(), "", []
    if not setting.is_full:
        choice = question_choice(marking_draft, setting.new_step_path)
        questions = choice.questions if choice else []
        try:
            methods = marking_draft.methods(setting.new_step_path, choice.question if choice else None)
        except GradeFlowError as error:
            unavailable_reason = str(error)
    add_prompt = _add_prompt(setting)
    if errors and not setting.step_paths:
        errors = [add_prompt]
    return StepsSettingView(
        setting,
        errors,
        children,
        questions,
        bool(choices(methods, workspace.offers)),
        unavailable_reason=unavailable_reason,
        label_controls=label_controls,
        needs_steps=needs_steps or bool(setting.minimum),
        add_prompt=add_prompt,
    )


@dataclass(frozen=True, kw_only=True)
class Editor:
    """The step at `path` of a marking being edited, as a form, with the cards of the steps it holds."""

    actions: EditorActions
    path: str
    form: ObjectForm
    label: str
    settings_holding_steps: list[StepsSettingView] = field(default_factory=list)
    description: str = ""
    breadcrumbs: list[tuple[str, str]] = field(default_factory=list)
    problems_elsewhere: list[tuple[str, str]] = field(default_factory=list)
    """Problems in other steps, shown when saving: each one's step path and text."""
    can_change_method: bool = False
    """Whether another method can mark here."""
    newer_version: CoreMethod | None = None
    """The latest version of the step's method, when it uses an earlier one."""
    archived: bool = False
    made_by: str = ""
    """Who made a saved method, and its version: "by Quinn QA · Version 2"; empty for a built-in one."""
    editable: bool = False
    saved_by_someone_else: bool = False
    """Someone saved the marking after the user started this draft."""
    more_open: bool = False
    discard_note: str = ""
    """What discarding the draft leaves, in words."""
    conflicts: Sequence[Conflict] = ()
    """Other markings of the questions this one marks."""

    @property
    def conflict_message(self) -> str:
        """Which questions another marking marks, in words."""
        keys = sorted({key for conflict in self.conflicts for key in conflict.questions})
        if not keys:
            return ""
        others = "has another marking" if len(keys) == 1 else "have other markings"
        return f"{get_text_list(keys, 'and')} {others}."


def editor(workspace: StepWorkspace, path: str, *, saving: bool = False, more_open: bool = False) -> Editor:
    """The editor at `path`, or at the marking itself if that's gone; with More options open after a change inside
    it."""
    try:
        return _editor(workspace, path, saving=saving, more_open=more_open)
    except GradeFlowError as error:
        form = ObjectForm({"type": "object", "properties": {}}, {}, data={})
        form.full_clean()
        form.add_error(None, str(error))
        return Editor(actions=workspace.actions, path="", form=form, label="Can't show this marking")


def _ancestor_paths(marking_draft: MarkingDraft, path: str) -> list[str]:
    """The paths of the steps from the top of the marking down to the one at `path`, which is last."""
    path_parts = path.split("/")
    ancestors = ("/".join(path_parts[:length]) for length in range(1, len(path_parts) + 1))
    return [ancestor for ancestor in ancestors if marking_draft.is_step(ancestor)]


def _editor(workspace: StepWorkspace, path: str, *, saving: bool, more_open: bool) -> Editor:
    marking_draft = workspace.marking_draft
    if not marking_draft.is_step(path):
        path = ""
    form = workspace.form(path)
    form.add_problems(workspace.problems_at(path, saving=saving))
    unfilled = [problem for problem in marking_draft.problems if problem.step_path == path and problem.is_incomplete]
    method = marking_draft.method(path)
    return Editor(
        actions=workspace.actions,
        path=path,
        form=form,
        settings_holding_steps=[
            _steps_setting_view(
                workspace,
                setting,
                form.steps_setting_errors.get(setting.name, []),
                form.label_controls.get(setting.name, ""),
                any(problem.field[:1] == (setting.name,) for problem in unfilled),
            )
            for setting in marking_draft.settings_holding_steps(path)
        ],
        label=marking_draft.label(path),
        description=marking_draft.describe(path),
        breadcrumbs=[(ancestor, marking_draft.label(ancestor)) for ancestor in _ancestor_paths(marking_draft, path)]
        if path
        else [],
        problems_elsewhere=[
            (problem.step_path, _problem_elsewhere_text(workspace, problem, path))
            for problem in marking_draft.problems
            if saving and problem.step_path != path
        ],
        can_change_method=len(choices(marking_draft.methods(path), workspace.offers)) > 1,
        newer_version=workspace.offers.newer_version(method, workspace.methods)
        if method and workspace.editable
        else None,
        archived=method is not None and method.name in workspace.offers.archived,
        made_by=_made_by(method, workspace.offers),
        editable=workspace.editable,
        saved_by_someone_else=workspace.saved_by_someone_else,
        more_open=form.has_advanced_errors or more_open,
        discard_note=workspace.discard_note,
        conflicts=workspace.conflicts,
    )
