"""What the landing page shows of its examples: the core's names and descriptions, and the marks it gave."""

import json
from dataclasses import dataclass
from functools import cache
from typing import Any

from django.utils.text import get_text_list
from gradeflow_core import BUILTINS, MarkScheme, Method

from gradeflow_app.landing.content import (
    Answer,
    Example,
    MarkRange,
    compatible_landing_methods,
    core_marks,
    landing_content,
)
from gradeflow_app.landing.forms import ExampleSettingsForm

GITHUB_URL = "https://github.com/gradeflowhq"
SECTIONS = {"grouping": "Grouping", "methods": "Methods", "canvas": "Canvas", "privacy": "Privacy"}


@dataclass(frozen=True)
class AnswerRow:
    answers: dict[str, str]
    student_count: int
    marks: float | None
    marks_before: float | None
    feedback: str

    @property
    def answer(self) -> str:
        """The answer to the question marked, which comes last."""
        return list(self.answers.values())[-1]

    @property
    def marks_changed(self) -> bool:
        return self.marks != self.marks_before


@dataclass(frozen=True)
class SettingVariant:
    value: str
    description: str
    rows: list[AnswerRow]


@dataclass(frozen=True)
class ShownExample:
    key: str
    example: Example
    method: Method[Any]
    initial_value: str
    variants: list[SettingVariant]


@dataclass(frozen=True)
class AnswerGroup:
    answers: tuple[Answer, ...]
    marks_before: MarkRange
    marks_when_accepted: MarkRange
    """Its marks once all its answers are accepted."""

    @property
    def student_count(self) -> int:
        return sum(answer.student_count for answer in self.answers)

    @property
    def texts_json(self) -> str:
        return json.dumps([answer.text for answer in self.answers])

    @property
    def is_accepted_initially(self) -> bool:
        return all(answer.text in landing_content().grouping.accepted_answers for answer in self.answers)


def other_methods() -> list[str]:
    """The names of the methods without an example, simplest first."""
    with_examples = {BUILTINS.find(example.method).label for example in landing_content().examples.values()}
    by_level = sorted(compatible_landing_methods(), key=lambda method: method.level != "basic")
    return list(dict.fromkeys(method.label for method in by_level if method.label not in with_examples))


@cache
def examples() -> list[ShownExample]:
    """Every example with each value of its setting: its markings as the core describes them, and its answers' marks
    beside those it starts with."""
    example_marks = core_marks()["examples"]
    initial_values = ExampleSettingsForm().initial_values()
    return [
        ShownExample(
            key,
            example,
            BUILTINS.find(example.method),
            initial_values[key],
            [
                SettingVariant(
                    value,
                    "\n\n".join(check.describe() for check in MarkScheme.load(markings).markings),
                    rows(example, example_marks[key][value], example_marks[key][initial_values[key]]),
                )
                for value, markings in example.markings.items()
            ],
        )
        for key, example in landing_content().examples.items()
    ]


def rows(
    example: Example, marks: list[tuple[float | None, str]], marks_before: list[tuple[float | None, str]]
) -> list[AnswerRow]:
    return [
        AnswerRow(answer.answers, answer.student_count, mark, mark_before, feedback)
        for answer, (mark, feedback), (mark_before, _) in zip(example.answers, marks, marks_before, strict=True)
    ]


@cache
def answer_groups() -> dict[str, list[AnswerGroup]]:
    """Q1's answers grouped by the same answer and by meaning, each group with its marks before and after its answers
    are accepted."""
    grouping = landing_content().grouping
    grouping_marks = core_marks()["grouping"]
    marks = {
        answer.text: (before, after)
        for answer, (before, _), (after, _) in zip(
            grouping.answers, grouping_marks["before_accepting"], grouping_marks["after_accepting"], strict=True
        )
    }

    def group(answers: list[Answer]) -> AnswerGroup:
        before, after = zip(*(marks[answer.text] for answer in answers), strict=True)
        return AnswerGroup(tuple(answers), MarkRange(min(before), max(before)), MarkRange(min(after), max(after)))

    by_meaning: dict[str, list[Answer]] = {}
    for answer in grouping.answers:
        by_meaning.setdefault(answer.meaning_group, []).append(answer)
    return {
        "by_answer": [group([answer]) for answer in grouping.answers],
        "by_meaning": [group(answers) for answers in by_meaning.values()],
    }


def landing_context() -> dict[str, Any]:
    """What the landing page shows, apart from the settings form."""
    landing = landing_content()
    return {
        "github_url": GITHUB_URL,
        "sections": SECTIONS,
        "workspace": landing.workspace,
        "grouping": landing.grouping,
        "answer_groups": answer_groups(),
        "examples": examples(),
        "other_methods": get_text_list(other_methods(), "and"),
        "privacy_students": landing.privacy_students,
    }
