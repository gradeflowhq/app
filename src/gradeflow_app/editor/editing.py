"""Forms and navigation shared by question markings and reusable method drafts."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, NamedTuple

from gradeflow_core import Check, GradeFlowError, Methods, Problem
from gradeflow_core.editing import Condition, EditingError, MarkingDraft, Position

from gradeflow_app.libraries.collection import OfferedMethods
from gradeflow_app.ui.schemaforms.forms import ObjectForm
from gradeflow_app.ui.schemaforms.schema import SUGGESTIONS_KEY, hint
from gradeflow_app.ui.schemaforms.widgets import ConditionWidget


@dataclass(frozen=True)
class EditorActions:
    """Where the editor sends edits and gets methods to choose from; and, for a question's marking, where it is
    saved, discarded or edited as YAML."""

    update: str
    methods: str
    yaml: str = ""
    discard: str = ""
    save: str = ""


class Conflict(NamedTuple):
    """Another marking of questions a marking marks."""

    questions: list[str]
    """The questions both mark."""
    name: str
    url: str


def problem_text(marking_draft: MarkingDraft, problem: Problem, open_path: str = "") -> str:
    """`problem` in words, after its step's `label`, unless that step is at `open_path`, and the title of the field
    at fault. As the core words it if the core can't say where."""
    try:
        where = (
            []
            if problem.step_path == open_path
            else [marking_draft.label(problem.step_path) if problem.step_path else "The marking"]
        )
        if problem.field:
            name = str(problem.field[0])
            where.append(marking_draft.schema(problem.step_path)["properties"].get(name, {}).get("title", name))
    except GradeFlowError:
        return str(problem)
    return f"{', '.join(where)}: {problem.message}" if where else problem.message


class StepWorkspace(ABC):
    @property
    @abstractmethod
    def marking_draft(self) -> MarkingDraft: ...

    @property
    @abstractmethod
    def offers(self) -> OfferedMethods:
        """The saved methods to offer."""

    @property
    @abstractmethod
    def methods(self) -> Methods:
        """Every method the marking may use, each version included."""

    @property
    @abstractmethod
    def actions(self) -> EditorActions: ...

    @property
    def editable(self) -> bool:
        return True

    @property
    def saved_by_someone_else(self) -> bool:
        return False

    @property
    def conflicts(self) -> list[Conflict]:
        return []

    @property
    def discard_note(self) -> str:
        """What discarding the draft leaves, in words."""
        return ""

    def suggestions_at(self, position: Position) -> dict[str, int]:
        return {}

    def form(self, path: str, **kwargs: Any) -> ObjectForm:
        schema = self.marking_draft.schema(path)
        if suggestions := self.suggestions_at(self.marking_draft.position(path)):
            properties = {
                name: {**property_schema, SUGGESTIONS_KEY: suggestions}
                if hint(property_schema).get("lists_answers")
                else property_schema
                for name, property_schema in schema["properties"].items()
            }
            schema = {**schema, "properties": properties}
        form = ObjectForm(schema, self.marking_draft.values(path), path=path, **kwargs)
        if conditions := [field for field in form.fields.values() if isinstance(field.widget, ConditionWidget)]:
            earlier = [(at, self.marking_draft.label(at)) for at in self.marking_draft.earlier_checks(path)]
            for field in conditions:
                field.widget.checks = earlier
        return form

    def editor_structure(self, path: str) -> tuple[object, ...]:
        """What redraws the editor at `path` when it changes: schema, methods offered and condition rows."""
        marking_draft = self.marking_draft
        rows = [value for value in marking_draft.values(path).values() if isinstance(value, Condition)]
        methods_and_conditions: list[object] = [tuple(method.ref for method in marking_draft.methods(path)), *rows]
        for setting in marking_draft.settings_holding_steps(path):
            try:
                methods_and_conditions.append(
                    tuple(method.ref for method in marking_draft.methods(setting.new_step_path))
                )
            except GradeFlowError as error:
                methods_and_conditions.append(str(error))
        return marking_draft.schema(path), tuple(methods_and_conditions)

    @property
    def finalized(self) -> Check | None:
        """The marking, once the core finds nothing wrong with it."""
        try:
            return self.marking_draft.finalized()
        except EditingError:
            return None

    def problems_at(self, path: str, *, saving: bool) -> list[Problem]:
        return [
            problem
            for problem in self.marking_draft.problems
            if problem.step_path == path and (saving or not problem.is_incomplete)
        ]

    @property
    def valid(self) -> bool:
        return not self.marking_draft.problems
