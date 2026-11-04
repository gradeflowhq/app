"""The check editor, for its templates: the check at a path as a form, with cards for the checks it holds."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from django.utils.text import get_text_list
from gradeflow_core import GradeFlowError, Methods, Problem
from gradeflow_core import Method as CoreMethod
from gradeflow_core.editing import ChecksSetting, MarkingDraft

from gradeflow_app.editor.editing import CheckWorkspace, Conflict, EditorActions
from gradeflow_app.editor.guide import choices, question_group_check
from gradeflow_app.libraries.collection import BUILTIN_NAMES, OfferedMethods
from gradeflow_app.ui.schemaforms.forms import ObjectForm


@dataclass(frozen=True)
class CheckCard:
    """A check in a setting, as a card that opens it."""

    path: str
    label: str
    description: str
    has_problems: bool
    problem: str
    """What is wrong with it or a check it holds, rather than not filled in yet."""
    notes: list[str]
    """What its place says about it: the question it reads, its weight, its condition."""


@dataclass(frozen=True)
class ChecksSettingView:
    setting: ChecksSetting
    errors: list[str]
    children: list[CheckCard]
    questions: list[str]
    """In a question group, the questions an added check may read, chosen before its method."""
    can_add: bool
    unavailable_reason: str = ""
    """Why checks can't be added yet, as the core says."""
    label_controls: str = ""
    needs_checks: bool = False
    """It still needs checks, which isn't shown as a mistake until saving."""

    @property
    def has_fixed_count(self) -> bool:
        """A list of exactly so many checks, such as one for each of a question's values, which none are added to or
        removed from."""
        return self.setting.is_list and self.setting.minimum == self.setting.maximum


def _made_by(method: CoreMethod[Any] | None, offers: OfferedMethods) -> str:
    if method is None or method.name in BUILTIN_NAMES:
        return ""
    owner = offers.owners.get(method.name)
    return f"by {owner} · Version {method.version}" if owner else f"Version {method.version}"


def _problem_elsewhere_text(workspace: CheckWorkspace, problem: Problem, open_path: str) -> str:
    """A problem of a check other than the one at `open_path`; one not filled in yet, as what to do."""
    label = workspace.checks.label(problem.check) if problem.check else "The marking"
    return f"Finish {label}." if problem.is_incomplete else workspace.problem_text(problem, open_path)


def _card(workspace: CheckWorkspace, path: str) -> CheckCard:
    """The check at `path` as a card, with the first problem of it or a check it holds."""
    checks = workspace.checks
    problems = [each for each in checks.problems if each.check == path or each.check.startswith(f"{path}.")]
    problem = next((each for each in problems if not each.is_incomplete), None)
    return CheckCard(
        path=path,
        label=checks.label(path),
        description=checks.describe(path),
        has_problems=bool(problems),
        problem=workspace.problem_text(problem, path) if problem else "",
        notes=checks.notes(path),
    )


def _checks_setting_view(
    workspace: CheckWorkspace, setting: ChecksSetting, errors: list[str], label_controls: str, needs_checks: bool
) -> ChecksSettingView:
    checks = workspace.checks
    children = [_card(workspace, child) for child in setting.checks]
    methods, unavailable_reason, questions = Methods(), "", []
    if not setting.is_full:
        group_check = question_group_check(checks, setting.new_check_path)
        questions = group_check.questions if group_check else []
        try:
            methods = checks.methods(setting.new_check_path, group_check.question if group_check else None)
        except GradeFlowError as error:
            unavailable_reason = str(error)
    if errors and not setting.checks:
        errors = ["Add a check." if not setting.is_list else "Add at least one check."]
    return ChecksSettingView(
        setting,
        errors,
        children,
        questions,
        bool(choices(methods, workspace.offers)),
        unavailable_reason=unavailable_reason,
        label_controls=label_controls,
        needs_checks=needs_checks or bool(setting.minimum),
    )


@dataclass(frozen=True, kw_only=True)
class Editor:
    """The check at `path` of a checks being edited, as a form, with the cards of the checks it holds."""

    actions: EditorActions
    path: str
    form: ObjectForm
    label: str
    checks_settings: list[ChecksSettingView] = field(default_factory=list)
    description: str = ""
    breadcrumbs: list[tuple[str, str]] = field(default_factory=list)
    problems_elsewhere: list[tuple[str, str]] = field(default_factory=list)
    """Problems elsewhere in the checks, shown when saving: each one's check, and it in words."""
    can_change_method: bool = False
    """Whether another method can mark here."""
    newer_version: CoreMethod[Any] | None = None
    """The latest version of the check's method, when it uses an earlier one."""
    archived: bool = False
    made_by: str = ""
    """Who made a saved method, and its version: "by Quinn QA · Version 2"; empty for a built-in one."""
    editable: bool = False
    saved_by_someone_else: bool = False
    """Someone saved the checks after the user started this draft."""
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


def editor(workspace: CheckWorkspace, path: str, *, saving: bool = False, more_open: bool = False) -> Editor:
    """The editor at `path`, or at the marking itself if that's gone; with More options open after a change inside
    it."""
    try:
        return _editor(workspace, path, saving=saving, more_open=more_open)
    except GradeFlowError as error:
        form = ObjectForm({"type": "object", "properties": {}}, {}, data={})
        form.full_clean()
        form.add_error(None, str(error))
        return Editor(actions=workspace.actions, path="", form=form, label="Can't show this marking")


def _ancestor_paths(checks: MarkingDraft, path: str) -> list[str]:
    """The paths of the checks from the checks down to the one at `path`, which is last."""
    steps = path.split(".")
    return [at for at in (".".join(steps[:end]) for end in range(len(steps) + 1)) if checks.is_check(at)]


def _editor(workspace: CheckWorkspace, path: str, *, saving: bool, more_open: bool) -> Editor:
    checks = workspace.checks
    if not checks.is_check(path):
        path = ""
    form = workspace.form(path)
    form.add_problems(workspace.problems_at(path, saving=saving))
    unfilled = [problem for problem in checks.problems if problem.check == path and problem.is_incomplete]
    method = checks.method(path)
    return Editor(
        actions=workspace.actions,
        path=path,
        form=form,
        checks_settings=[
            _checks_setting_view(
                workspace,
                setting,
                form.checks_setting_errors.get(setting.name, []),
                form.label_controls.get(setting.name, ""),
                any(problem.field[:1] == (setting.name,) for problem in unfilled),
            )
            for setting in checks.checks_settings(path)
        ],
        label=checks.label(path),
        description=checks.describe(path),
        breadcrumbs=[(ancestor, checks.label(ancestor)) for ancestor in _ancestor_paths(checks, path)] if path else [],
        problems_elsewhere=[
            (problem.check, _problem_elsewhere_text(workspace, problem, path))
            for problem in checks.problems
            if saving and problem.check != path
        ],
        can_change_method=len(choices(checks.methods(path), workspace.offers)) > 1,
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
