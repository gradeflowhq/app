"""The check editor, for its templates: the check at a path as a form, with cards for the checks it holds."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from gradeflow_core import GradeFlowError, Methods, Problem
from gradeflow_core import Method as CoreMethod
from gradeflow_core.editing import MarkingDraft, Slot

from gradeflow_app.editor.editing import CheckWorkspace, Conflict, EditorActions
from gradeflow_app.editor.guide import choices, listed, question_group_check
from gradeflow_app.libraries.collection import BUILTIN_NAMES, Offers
from gradeflow_app.ui.schemaforms.forms import ObjectForm


@dataclass(frozen=True)
class Child:
    """A check in a slot, as a card that opens it."""

    path: str
    label: str
    description: str
    incomplete: bool
    problem: str
    """What is wrong with it or a check it holds, rather than not filled in yet."""
    notes: list[str]
    """What its place says about it: the question it reads, its weight, its condition."""


@dataclass(frozen=True)
class SlotView:
    slot: Slot
    errors: list[str]
    children: list[Child]
    questions: list[str]
    """In a question group, the questions an added check may read, chosen before its method."""
    can_add: bool
    unavailable: str = ""
    """Why checks can't be added yet, as the core says."""
    addon: str = ""
    needed: bool = False
    """It still needs checks, which isn't shown as a mistake until saving."""

    @property
    def fixed(self) -> bool:
        """A list of exactly so many checks, such as one for each of a question's values, which none are added to or
        removed from."""
        return self.slot.many and self.slot.minimum == self.slot.maximum


def _made_by(method: CoreMethod[Any] | None, offers: Offers) -> str:
    if method is None or method.name in BUILTIN_NAMES:
        return ""
    owner = offers.owners.get(method.name)
    return f"by {owner} · Version {method.version}" if owner else f"Version {method.version}"


def _elsewhere(workspace: CheckWorkspace, problem: Problem, here: str) -> str:
    """A problem of a check other than the one at `here`; one not filled in yet, as what to do."""
    label = workspace.label(problem.check) if problem.check else "The marking"
    return f"Finish {label}." if problem.incomplete else workspace.said(problem, here)


def _on_card(workspace: CheckWorkspace, path: str) -> str:
    """The first problem of the check at `path` or a check it holds, as its card says it."""
    problem = next((each for each in workspace.problems_in(path) if not each.incomplete), None)
    return workspace.said(problem, path) if problem else ""


def _slot(workspace: CheckWorkspace, slot: Slot, errors: list[str], addon: str, needed: bool) -> SlotView:
    checks = workspace.checks
    children = [
        Child(
            path=child,
            label=workspace.label(child),
            description=checks.describe(child),
            incomplete=workspace.incomplete(child),
            problem=_on_card(workspace, child),
            notes=checks.notes(child),
        )
        for child in slot.checks
    ]
    offered, unavailable, questions = Methods(), "", []
    if not slot.full:
        in_group = question_group_check(checks, slot.new)
        questions = in_group.questions if in_group else []
        try:
            offered = checks.methods(slot.new, in_group.question if in_group else None)
        except GradeFlowError as error:
            unavailable = str(error)
    if errors and not slot.checks:
        errors = ["Add a check." if not slot.many else "Add at least one check."]
    return SlotView(
        slot,
        errors,
        children,
        questions,
        bool(choices(offered, workspace.offers)),
        unavailable=unavailable,
        addon=addon,
        needed=needed or bool(slot.minimum),
    )


@dataclass(frozen=True, kw_only=True)
class Editor:
    """The check at `path` of a checks being edited, as a form, with the cards of the checks it holds."""

    actions: EditorActions
    path: str
    form: ObjectForm
    label: str
    slots: list[SlotView] = field(default_factory=list)
    description: str = ""
    crumbs: list[tuple[str, str]] = field(default_factory=list)
    elsewhere: list[tuple[str, str]] = field(default_factory=list)
    """Problems elsewhere in the checks, shown when saving: each one's check, and it in words."""
    changeable: bool = False
    """Whether another method can mark here."""
    newer: CoreMethod[Any] | None = None
    """The latest version of the check's method, when it uses an earlier one."""
    archived: bool = False
    made_by: str = ""
    """Who made a saved method, and its version: "by Quinn QA · Version 2"; empty for a built-in one."""
    editable: bool = False
    stale: bool = False
    """Someone saved the checks after the user started this draft."""
    more_open: bool = False
    kept: str = ""
    """What discarding the draft leaves, in words."""
    conflicts: Sequence[Conflict] = ()
    """Other markings of the questions this one marks."""

    @property
    def conflict(self) -> str:
        """Which questions another marking marks, in words."""
        keys = sorted({key for conflict in self.conflicts for key in conflict.questions})
        if not keys:
            return ""
        return f"{listed(keys)} has another marking." if len(keys) == 1 else f"{listed(keys)} have other markings."

    @property
    def holds_checks(self) -> bool:
        """Whether it holds checks, shown as cards that say what its summary would."""
        return bool(self.slots)


def editor(workspace: CheckWorkspace, path: str, *, saving: bool = False, more: bool = False) -> Editor:
    """The editor at `path`, or at the marking itself if that's gone; with `more` open after a change inside it."""
    try:
        return _editor(workspace, path, saving=saving, more=more)
    except GradeFlowError as error:
        form = ObjectForm({"type": "object", "properties": {}}, {}, data={})
        form.full_clean()
        form.add_error(None, str(error))
        return Editor(actions=workspace.actions, path="", form=form, label="Can't show this marking")


def _holding(checks: MarkingDraft, path: str) -> list[str]:
    """The paths of the checks from the checks down to the one at `path`, which is last."""
    steps = path.split(".")
    return [at for at in (".".join(steps[:end]) for end in range(len(steps) + 1)) if checks.is_check(at)]


def _editor(workspace: CheckWorkspace, path: str, *, saving: bool, more: bool) -> Editor:
    checks = workspace.checks
    if not checks.is_check(path):
        path = ""
    form = workspace.form(path)
    form.add_problems(workspace.problems_at(path, saving=saving))
    unfilled = [problem for problem in workspace.problems if problem.check == path and problem.incomplete]
    method = checks.method(path)
    return Editor(
        actions=workspace.actions,
        path=path,
        form=form,
        slots=[
            _slot(
                workspace,
                slot,
                form.slot_errors.get(slot.name, []),
                form.addons.get(slot.name, ""),
                any(problem.field[:1] == (slot.name,) for problem in unfilled),
            )
            for slot in checks.slots(path)
        ],
        label=workspace.label(path),
        description=checks.describe(path),
        crumbs=[(at, workspace.label(at)) for at in _holding(checks, path)] if path else [],
        elsewhere=[
            (problem.check, _elsewhere(workspace, problem, path))
            for problem in workspace.problems_elsewhere(path)
            if saving
        ],
        changeable=len(choices(checks.methods(path), workspace.offers)) > 1,
        newer=workspace.offers.newer(method, workspace.methods) if method and workspace.editable else None,
        archived=method is not None and method.name in workspace.offers.archived,
        made_by=_made_by(method, workspace.offers),
        editable=workspace.editable,
        stale=workspace.stale,
        more_open=form.more_open or more,
        kept=workspace.kept,
        conflicts=workspace.conflicts,
    )
