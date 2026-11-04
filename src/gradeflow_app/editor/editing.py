"""Forms and navigation shared by question markings and reusable method drafts."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, NamedTuple

from gradeflow_core import GradeFlowError, Methods, Problem
from gradeflow_core.editing import Condition, EditingError, MarkingDraft, Position
from gradeflow_core.editing import Marking as CoreMarking

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


def problem_text(checks: MarkingDraft, problem: Problem, open_path: str = "") -> str:
    """`problem` in words, after where it is: the `label` of its check, unless that is the check at `open_path`, then
    the title of the setting at fault. Where the core can't say, the problem as the core words it."""
    try:
        where = [] if problem.check == open_path else [checks.label(problem.check) if problem.check else "The marking"]
        if problem.field:
            name = str(problem.field[0])
            where.append(checks.schema(problem.check)["properties"].get(name, {}).get("title", name))
    except GradeFlowError:
        return str(problem)
    return f"{', '.join(where)}: {problem.message}" if where else problem.message


class CheckWorkspace(ABC):
    @property
    @abstractmethod
    def checks(self) -> MarkingDraft: ...

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
        schema = self.checks.schema(path)
        if suggestions := self.suggestions_at(self.checks.position(path)):
            properties = {
                name: {**property_schema, SUGGESTIONS_KEY: suggestions}
                if hint(property_schema).get("lists_answers")
                else property_schema
                for name, property_schema in schema["properties"].items()
            }
            schema = {**schema, "properties": properties}
        form = ObjectForm(schema, self.checks.values(path), path=path, **kwargs)
        if conditions := [field for field in form.fields.values() if isinstance(field.widget, ConditionWidget)]:
            earlier = [(at, self.checks.label(at)) for at in self.checks.earlier_checks(path)]
            for field in conditions:
                field.widget.checks = earlier
        return form

    def editor_inputs(self, path: str) -> tuple[object, ...]:
        """What the editor at `path` asks for: its fields, the methods it offers, and the conditions it shows as
        rows."""
        checks = self.checks
        rows = [value for value in checks.values(path).values() if isinstance(value, Condition)]
        offered: list[object] = [tuple(method.ref for method in checks.methods(path)), *rows]
        for setting in checks.checks_settings(path):
            try:
                offered.append(tuple(method.ref for method in checks.methods(setting.new_check_path)))
            except GradeFlowError as error:
                offered.append(str(error))
        return checks.schema(path), tuple(offered)

    @property
    def finalized(self) -> CoreMarking | None:
        """The marking, once the core finds nothing wrong with it."""
        try:
            return self.checks.finalized()
        except EditingError:
            return None

    def problems_at(self, path: str, *, saving: bool) -> list[Problem]:
        return [
            problem
            for problem in self.checks.problems
            if problem.check == path and (saving or not problem.is_incomplete)
        ]

    @property
    def valid(self) -> bool:
        return not self.checks.problems
