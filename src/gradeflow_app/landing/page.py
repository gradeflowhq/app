"""What the landing page shows of its examples: the core's names and descriptions, and the marks it gave."""

import json
from dataclasses import dataclass
from functools import cache
from typing import Any

from gradeflow_core import BUILTINS, MarkScheme, Method

from gradeflow_app.landing.content import Answer, Example, Marks, compatible, content, marks
from gradeflow_app.landing.forms import Settings

GITHUB = "https://github.com/gradeflowhq"
SECTIONS = {"grouping": "Grouping", "methods": "Methods", "canvas": "Canvas", "privacy": "Privacy"}


@dataclass(frozen=True)
class Row:
    answers: dict[str, str]
    students: int
    marks: Marks | None
    before: Marks | None
    feedback: str

    @property
    def answer(self) -> str:
        """The answer to the question marked, which comes last."""
        return list(self.answers.values())[-1]

    @property
    def code(self) -> bool:
        return "\n" in self.answer

    @property
    def changed(self) -> bool:
        return self.marks != self.before


@dataclass(frozen=True)
class Variant:
    value: str
    description: str
    rows: list[Row]


@dataclass(frozen=True)
class Shown:
    key: str
    example: Example
    method: Method[Any]
    start: str
    variants: list[Variant]


@dataclass(frozen=True)
class AnswerGroup:
    answers: tuple[Answer, ...]
    before: Marks
    accepted: Marks
    """Its marks once all its answers are accepted."""

    @property
    def students(self) -> int:
        return sum(answer.students for answer in self.answers)

    @property
    def texts_json(self) -> str:
        return json.dumps([answer.text for answer in self.answers])

    @property
    def accepted_at_first(self) -> bool:
        return all(answer.text in content().grouping.accepted for answer in self.answers)


def other_methods() -> list[str]:
    """The names of the methods without an example, simplest first."""
    with_examples = {BUILTINS.by_ref[example.method].label for example in content().examples.values()}
    by_level = sorted(compatible(), key=lambda info: info.level != "basic")
    return list(dict.fromkeys(info.label for info in by_level if info.label not in with_examples))


@cache
def examples() -> list[Shown]:
    """Every example with each value of its setting: its markings as the core describes them, and its answers' marks
    beside those it starts with."""
    core = marks()["examples"]
    start = Settings().initial_values()
    return [
        Shown(
            key,
            example,
            BUILTINS.by_ref[example.method],
            start[key],
            [
                Variant(
                    value,
                    "\n\n".join(check.describe() for check in MarkScheme.load(markings).markings),
                    rows(example, core[key][value], core[key][start[key]]),
                )
                for value, markings in example.markings.items()
            ],
        )
        for key, example in content().examples.items()
    ]


def rows(example: Example, marked: list[tuple[float | None, str]], before: list[tuple[float | None, str]]) -> list[Row]:
    def single(value: float | None) -> Marks | None:
        return None if value is None else Marks(value, value)

    return [
        Row(answered.answers, answered.students, single(value), single(was), feedback)
        for answered, (value, feedback), (was, _) in zip(example.answers, marked, before, strict=True)
    ]


@cache
def answer_groups() -> dict[str, list[AnswerGroup]]:
    """Q1's answers grouped by the same answer and by meaning, each group with its marks before and after its answers
    are accepted."""
    grouping = content().grouping
    core = marks()["grouping"]
    marked = {
        answer.text: (before, after)
        for answer, (before, _), (after, _) in zip(grouping.answers, core["before"], core["after"], strict=True)
    }

    def group(answers: list[Answer]) -> AnswerGroup:
        before, after = zip(*(marked[answer.text] for answer in answers), strict=True)
        return AnswerGroup(tuple(answers), Marks.of(list(before)), Marks.of(list(after)))

    by_meaning: dict[str, list[Answer]] = {}
    for answer in grouping.answers:
        by_meaning.setdefault(answer.meaning, []).append(answer)
    return {
        "answers": [group([answer]) for answer in grouping.answers],
        "meaning": [group(answers) for answers in by_meaning.values()],
    }


def context() -> dict[str, Any]:
    """What the landing page shows, apart from the settings form."""
    landing = content()
    return {
        "github": GITHUB,
        "sections": SECTIONS,
        "workspace": landing.workspace,
        "grouping": landing.grouping,
        "answer_groups": answer_groups(),
        "examples": examples(),
        "other_methods": other_methods(),
        "students": landing.students,
    }
