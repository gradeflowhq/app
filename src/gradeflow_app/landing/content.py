"""The landing page's examples, in examples.json, and the marks the core gave them ahead of time, in marks.json."""

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from gradeflow_core import BUILTINS, Method, QuestionSet
from gradeflow_core.editing import MarkingDraft, Position, compatible_methods
from pydantic import BaseModel, ConfigDict

from gradeflow_app.ui.schemaforms.schema import hint, resolve

EXAMPLES = Path(__file__).with_name("examples.json")
MARKS = Path(__file__).with_name("marks.json")
"""What the core marks: each example's answers with every value of its setting, the workspace's answers with its
marking and each of its checks, and Q1's answers before and after they are all accepted. tests/test_landing.py checks
them, and writes them when run."""


type MarkingJson = dict[str, Any]
"""A marking's JSON as the core takes it, with ``question`` naming the question it marks unless it is a question
group's."""


class LandingModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True)
class MarkRange:
    """Marks from low to high: one mark when they are the same."""

    low: float
    high: float


# The workspace in the hero: one question's answers grouped by the checks they pass.


class WorkspaceQuestion(LandingModel):
    question_key: str
    method: str | None = None
    average: int | None = None
    grouped: bool = False


class WorkspaceCheck(LandingModel):
    name: str
    """What it checks, in a word or two, beside each answer."""
    marking: MarkingJson
    """It alone, as Q5's marking."""


class WorkspaceAnswerGroup(LandingModel):
    answer: str
    other_answer_count: int
    student_count: int


class Workspace(LandingModel):
    course: str
    assessment: str
    question_key: str
    question_text: str
    question_set: QuestionSet
    questions: list[WorkspaceQuestion]
    marking: MarkingJson
    """The marking combining the checks."""
    setting_name: str
    """The marking's setting shown beside it."""
    checks: list[WorkspaceCheck]
    answer_groups: list[WorkspaceAnswerGroup]

    def marking_draft(self, marking: MarkingJson) -> MarkingDraft:
        return MarkingDraft(marking, self.question_set, self.question_key)

    @property
    def method_label(self) -> str:
        return self.marking_draft(self.marking).label("")

    @property
    def setting_label(self) -> tuple[str, str]:
        """The core's title for the marking's setting, and its label for the value it has."""
        draft = self.marking_draft(self.marking)
        schema = draft.schema("")
        setting = resolve(schema["properties"][self.setting_name], schema.get("$defs", {}))
        return setting["title"], hint(setting)["labels"][draft.values("")[self.setting_name]]

    @property
    def check_descriptions(self) -> list[tuple[str, str]]:
        """Each check, as the core names and describes it."""
        drafts = (self.marking_draft(check.marking) for check in self.checks)
        return [(draft.label(""), draft.describe("")) for draft in drafts]

    @property
    def questions_with_methods(self) -> list[tuple[WorkspaceQuestion, str | None]]:
        """Each question, with the name of the method that marks it."""
        return [(question, question.method and BUILTINS.by_ref[question.method].label) for question in self.questions]

    @property
    def max_marks(self) -> float:
        return self.question_set[self.question_key].max_marks

    @property
    def marked_count(self) -> int:
        return sum(1 for question in self.questions if question.method)

    @property
    def rows(self) -> list[tuple[WorkspaceAnswerGroup, float, list[tuple[WorkspaceCheck, bool]]]]:
        workspace_marks = core_marks()["workspace"]
        return [
            (group, mark, list(zip(self.checks, correct, strict=True)))
            for group, (mark, _), correct in zip(
                self.answer_groups,
                workspace_marks["marks"],
                zip(*workspace_marks["correct"], strict=True),
                strict=True,
            )
        ]


# Grouping: Q1's answers, alone or with others that mean the same, and marked by the answers accepted.


class Answer(LandingModel):
    text: str
    student_count: int
    meaning_group: str
    """The group BAAI/bge-small-en-v1.5 puts it in, at GradeFlow's default of 85% similar."""


class Grouping(LandingModel):
    question_key: str
    question_text: str
    question_set: QuestionSet
    accepted_answers: list[str]
    answers: list[Answer]
    markings: dict[str, list[MarkingJson]]
    """Its marking before and after all its answers are accepted."""

    @property
    def accepted_json(self) -> str:
        return json.dumps(self.accepted_answers)


# The methods: an example of each, with one setting to change.


class ExampleAnswer(LandingModel):
    answers: dict[str, str]
    """Each answer, by question."""
    student_count: int


class Example(LandingModel):
    method: str
    question_key: str
    question_text: str
    marked_question: str
    """The question whose marks it shows."""
    question_set: QuestionSet
    answers: list[ExampleAnswer]
    choices: dict[str, str] = {}
    """Its setting's values and their labels, when it has a list of them."""
    markings: dict[str, list[MarkingJson]]
    """Its markings for each value of its setting, as the browser submits it."""

    @property
    def max_marks(self) -> float:
        return self.question_set[self.marked_question].max_marks


class LandingContent(LandingModel):
    workspace: Workspace
    grouping: Grouping
    examples: dict[str, Example]
    privacy_students: list[tuple[str, float]]
    """The privacy example's students: ID and total, out of 60."""


@cache
def landing_content() -> LandingContent:
    return LandingContent.model_validate_json(EXAMPLES.read_text())


@cache
def core_marks() -> dict[str, Any]:
    marks: dict[str, Any] = json.loads(MARKS.read_text())
    return marks


@cache
def compatible_landing_methods() -> list[Method[Any]]:
    """The core's methods for the page's questions."""
    landing = landing_content()
    question_sets = [landing.workspace.question_set, landing.grouping.question_set]
    question_sets += [example.question_set for example in landing.examples.values()]
    methods = (
        method
        for question_set in question_sets
        for question in [None, *question_set]
        for method in compatible_methods(Position(question_set, question, in_group=question is None))
    )
    return list({method.ref: method for method in methods}.values())
