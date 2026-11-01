"""The test answers a method is tried on: its questions, test values and submissions, kept as JSON.

A submission answers every question; its answers stay together however rows are added, cleared or deleted. Those
chosen as examples become the method's example.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from django import forms
from gradeflow_core import GradeFlowError, Question, QuestionResult, QuestionSet
from gradeflow_core.editing import ExampleRow, RowResult

from gradeflow_app.ui.schemaforms.forms import ObjectForm

MAX_ROWS = 200


class Row(TypedDict):
    answers: dict[str, str]
    example: bool


class Tests(TypedDict, total=False):
    questions: dict[str, Any]
    test_values: dict[str, Any]
    rows: list[Row]
    example_at: int | None
    """Where the example the chosen rows make is among the method's examples."""


def new_tests(questions: QuestionSet) -> Tests:
    return {"questions": questions.model_dump(mode="json"), "test_values": {}, "rows": [], "example_at": None}


def questions_of(tests: Tests) -> QuestionSet:
    return QuestionSet.model_validate(tests.get("questions", {}))


def rows_of(tests: Tests) -> list[ExampleRow]:
    return [ExampleRow(answers=row["answers"]) for row in tests.get("rows", [])]


def examples_of(tests: Tests, results: Sequence[RowResult] | None = None) -> list[ExampleRow]:
    """The submissions chosen as examples, with what `results`, a run of every submission, gave them."""
    rows = tests.get("rows", [])
    marked: Sequence[RowResult | None] = results if results is not None else [None] * len(rows)
    return [
        ExampleRow(answers=row["answers"]) if result is None else result.as_example()
        for row, result in zip(rows, marked, strict=True)
        if row["example"]
    ]


def add_answer(tests: Tests, question: str, text: str) -> None:
    rows = tests.setdefault("rows", [])
    if len(rows) >= MAX_ROWS:
        raise GradeFlowError(f"Keep at most {MAX_ROWS} test answers.")
    answers = dict.fromkeys(tests.get("questions", {}), "")
    rows.append({"answers": answers | {question: text}, "example": False})


def set_answer(tests: Tests, row: int, question: str, text: str) -> None:
    _row(tests, row)["answers"][question] = text


def delete_row(tests: Tests, row: int) -> None:
    _row(tests, row)
    tests["rows"].pop(row)


def toggle_example(tests: Tests, row: int) -> None:
    found = _row(tests, row)
    found["example"] = not found["example"]


def _row(tests: Tests, row: int) -> Row:
    rows = tests.get("rows", [])
    if not 0 <= row < len(rows):
        raise GradeFlowError("This answer is no longer there.")
    return rows[row]


def set_question(tests: Tests, key: str, question: Question) -> str:
    """Change the question `key`, or add `question` when `key` is new; its key."""
    questions = tests.setdefault("questions", {})
    if not key:
        key = next(f"Q{n}" for n in range(1, len(questions) + 2) if f"Q{n}" not in questions)
        for row in tests.get("rows", []):
            row["answers"][key] = ""
    questions[key] = question.model_dump(mode="json")
    return key


def duplicate_question(tests: Tests, key: str) -> str:
    copy = set_question(tests, "", questions_of(tests)[key])
    for row in tests.get("rows", []):
        row["answers"][copy] = row["answers"].get(key, "")
    return copy


def remove_question(tests: Tests, key: str) -> None:
    tests.get("questions", {}).pop(key, None)
    for row in tests.get("rows", []):
        row["answers"].pop(key, None)


def answers_to(tests: Tests, question: str) -> list[str]:
    return [row["answers"].get(question, "") for row in tests.get("rows", [])]


def answer_form(
    question: Question | None, answer: str, key: str, data: Mapping[str, Any] | None = None
) -> ObjectForm | None:
    """The values of an answer to `question` to fill in, for a question whose answers have them; `key` keeps its
    fields' ids apart from other answers'."""
    if question is None or (schema := question.answer_schema()).get("type") != "object":
        return None
    value = question.answer_values(answer)
    form = ObjectForm(schema, value, prefix="value", auto_id=f"id_{key}_%s", data=data)
    for field in form.fields.values():
        if isinstance(field.widget, forms.TextInput):
            field.widget.attrs |= {"placeholder": field.label, "aria-label": field.label, "class": "input-sm"}
    return form


def posted_answer(question: Question | None, data: Mapping[str, Any]) -> str:
    """The answer to `question` a test answer's form posted: written whole, or value by value."""
    form = answer_form(question, "", "posted", data)
    if question is None or form is None:
        return str(data.get("answer", ""))
    form.full_clean()
    return question.answer_from(form.values())


def values_shown(question: Question | None, answer: str) -> list[tuple[str, str]] | None:
    """Each value of an answer of several values, a list's items one by one, with what it is; None for an answer
    written whole or as one value."""
    if question is None or not answer or (schema := question.answer_schema()).get("type") != "object":
        return None
    values = question.answer_values(answer)
    if len(schema["properties"]) < 2 and not any(isinstance(value, list) for value in values.values()):
        return None
    return [
        (prop.get("title", name), "" if item is None else str(item))
        for name, prop in schema["properties"].items()
        for item in _items(values.get(name))
    ]


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


@dataclass(frozen=True)
class Listed:
    """A submission as the answers column shows it: its answer to one question, and what the last run gave it."""

    index: int
    answer: str
    example: bool
    result: QuestionResult | None
    marks: float | None
    form: ObjectForm | None = None
    """The answer's values to change one by one, for a question whose answers have them."""
    values: list[tuple[str, str]] | None = None
    """Each value of an answer of several values, with what it is."""

    @property
    def number(self) -> int:
        return self.index + 1


def listed(tests: Tests, question: str, results: list[RowResult] | None, shown: Question | None = None) -> list[Listed]:
    """Each submission's answer to `question`, with what the run of `results` gave it, if it marked these answers."""
    rows = tests.get("rows", [])
    marked = results or []
    found = []
    for index, row in enumerate(rows):
        result = marked[index] if index < len(marked) and marked[index].answers == row["answers"] else None
        own = result.results.get(question) if result is not None else None
        marks = own.marks if own is not None else None
        answer = row["answers"].get(question, "")
        form = answer_form(shown, answer, f"answer{index}")
        found.append(Listed(index, answer, row["example"], own, marks, form, values_shown(shown, answer)))
    return found
