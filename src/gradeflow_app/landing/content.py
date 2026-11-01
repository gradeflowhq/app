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


type Marking = dict[str, Any]
"""A marking as the core takes it, with ``question`` naming the question it marks unless it is a question
group's."""


class Data(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


@dataclass(frozen=True)
class Marks:
    """Marks from low to high: one mark when they are the same."""

    low: float
    high: float

    @classmethod
    def of(cls, values: list[float]) -> Marks:
        return cls(min(values), max(values))


# The workspace in the hero: one question's answers grouped by the checks they pass.


class HeroQuestion(Data):
    number: str
    method: str | None = None
    average: int | None = None
    grouped: bool = False


class HeroCheck(Data):
    name: str
    """What it checks, in a word or two, beside each answer."""
    check: Marking
    """It alone, as Q5's marking."""


class HeroGroup(Data):
    code: str
    others: int
    students: int


class HeroWorkspace(Data):
    course: str
    assessment: str
    number: str
    question: str
    question_set: QuestionSet
    questions: list[HeroQuestion]
    marking: Marking
    """The marking combining the checks."""
    setting: str
    """The marking's setting shown beside it."""
    checks: list[HeroCheck]
    answers: list[HeroGroup]

    def draft(self, marking: Marking) -> MarkingDraft:
        return MarkingDraft(marking, self.question_set, self.number)

    @property
    def method(self) -> str:
        return self.draft(self.marking).label("")

    @property
    def aggregation(self) -> tuple[str, str]:
        """The core's title for the marking's setting, and its label for the value it has."""
        draft = self.draft(self.marking)
        schema = draft.schema("")
        setting = resolve(schema["properties"][self.setting], schema.get("$defs", {}))
        return setting["title"], hint(setting)["labels"][draft.values("")[self.setting]]

    @property
    def cards(self) -> list[tuple[str, str]]:
        """Each check, as the core names and describes it."""
        return [(draft.label(""), draft.describe("")) for draft in (self.draft(check.check) for check in self.checks)]

    @property
    def listed(self) -> list[tuple[HeroQuestion, str | None]]:
        """Each question, with the name of the method that marks it."""
        return [(question, question.method and BUILTINS.by_ref[question.method].label) for question in self.questions]

    @property
    def max_marks(self) -> float:
        return self.question_set[self.number].max_marks

    @property
    def marked(self) -> int:
        return sum(1 for question in self.questions if question.method)

    @property
    def rows(self) -> list[tuple[HeroGroup, Marks, list[tuple[HeroCheck, bool]]]]:
        core = marks()["workspace"]
        return [
            (group, Marks(value, value), list(zip(self.checks, correct, strict=True)))
            for group, (value, _), correct in zip(
                self.answers, core["marks"], zip(*core["correct"], strict=True), strict=True
            )
        ]


# Grouping: Q1's answers, alone or with others that mean the same, and marked by the answers accepted.


class Answer(Data):
    text: str
    students: int
    meaning: str
    """The group BAAI/bge-small-en-v1.5 puts it in, at GradeFlow's default of 85% similar."""


class Grouping(Data):
    number: str
    question: str
    question_set: QuestionSet
    accepted: list[str]
    answers: list[Answer]
    markings: dict[str, list[Marking]]
    """Its marking before and after all its answers are accepted."""

    @property
    def accepted_json(self) -> str:
        return json.dumps(self.accepted)


# The methods: an example of each, with one setting to change.


class Answered(Data):
    answers: dict[str, str]
    """Each answer, by question."""
    students: int


class Example(Data):
    method: str
    number: str
    question: str
    marked: str
    """The question whose marks it shows."""
    question_set: QuestionSet
    answers: list[Answered]
    choices: dict[str, str] = {}
    """Its setting's values and their labels, when it has a list of them."""
    markings: dict[str, list[Marking]]
    """Its markings for each value of its setting, as the browser submits it."""

    @property
    def max_marks(self) -> float:
        return self.question_set[self.marked].max_marks


class Content(Data):
    workspace: HeroWorkspace
    grouping: Grouping
    examples: dict[str, Example]
    students: list[tuple[str, float]]
    """The privacy example's students: ID and total, out of 60."""


@cache
def content() -> Content:
    return Content.model_validate_json(EXAMPLES.read_text())


@cache
def marks() -> dict[str, Any]:
    core: dict[str, Any] = json.loads(MARKS.read_text())
    return core


@cache
def compatible() -> list[Method[Any]]:
    """The core's methods for the page's questions."""
    landing = content()
    question_sets = [landing.workspace.question_set, landing.grouping.question_set]
    question_sets += [example.question_set for example in landing.examples.values()]
    found = (
        info
        for question_set in question_sets
        for question in [None, *question_set]
        for info in compatible_methods(Position(question_set, question, in_group=question is None))
    )
    return list({info.ref: info for info in found}.values())
