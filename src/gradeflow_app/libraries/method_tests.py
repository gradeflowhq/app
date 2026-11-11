"""The test answers a method is tried on: its questions, test values and rows of test answers, kept as JSON.

A row holds a test answer to every question; its answers stay together however rows are added, cleared or deleted.
Rows chosen as examples become the method's example.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypedDict

from django import forms
from gradeflow_core import GradeFlowError, Question, QuestionResult, QuestionSet
from gradeflow_core.editing import ExampleRow, RowResult

from gradeflow_app.ui.schemaforms.forms import ObjectForm

MAX_ROWS = 200


class TestAnswerRow(TypedDict):
    answers: dict[str, str]
    is_example: bool


class MethodTests(TypedDict, total=False):
    questions: dict[str, Any]
    test_values: dict[str, Any]
    rows: list[TestAnswerRow]
    example_index: int | None
    """Where the example the chosen rows make is among the method's examples."""


def new_tests(questions: QuestionSet) -> MethodTests:
    return {"questions": questions.model_dump(mode="json"), "test_values": {}, "rows": [], "example_index": None}


def questions_of(tests: MethodTests) -> QuestionSet:
    return QuestionSet.model_validate(tests.get("questions", {}))


def rows_of(tests: MethodTests) -> list[ExampleRow]:
    return [ExampleRow(answers=row["answers"]) for row in tests.get("rows", [])]


def examples_of(tests: MethodTests, results: Sequence[RowResult] | None = None) -> list[ExampleRow]:
    """The rows chosen as examples, with what `results`, a run of every row, gave them."""
    rows = tests.get("rows", [])
    row_results: Sequence[RowResult | None] = results if results is not None else [None] * len(rows)
    return [
        ExampleRow(answers=row["answers"]) if result is None else result.as_example()
        for row, result in zip(rows, row_results, strict=True)
        if row["is_example"]
    ]


def add_answer(tests: MethodTests, question: str, text: str) -> None:
    rows = tests.setdefault("rows", [])
    if len(rows) >= MAX_ROWS:
        raise GradeFlowError(f"Keep at most {MAX_ROWS} test answers.")
    answers = dict.fromkeys(tests.get("questions", {}), "")
    rows.append({"answers": answers | {question: text}, "is_example": False})


def set_answer(tests: MethodTests, row: int, question: str, text: str) -> None:
    _row(tests, row)["answers"][question] = text


def delete_row(tests: MethodTests, row: int) -> None:
    _row(tests, row)
    tests["rows"].pop(row)


def toggle_example(tests: MethodTests, row: int) -> None:
    test_answer = _row(tests, row)
    test_answer["is_example"] = not test_answer["is_example"]


def _row(tests: MethodTests, row: int) -> TestAnswerRow:
    rows = tests.get("rows", [])
    if not 0 <= row < len(rows):
        raise GradeFlowError("This answer is no longer there.")
    return rows[row]


def set_question(tests: MethodTests, key: str, question: Question) -> str:
    """Change the question `key`, or add `question` when `key` is new; its key."""
    questions = tests.setdefault("questions", {})
    if not key:
        key = next(f"Q{number}" for number in range(1, len(questions) + 2) if f"Q{number}" not in questions)
        for row in tests.get("rows", []):
            row["answers"][key] = ""
    questions[key] = question.model_dump(mode="json")
    return key


def duplicate_question(tests: MethodTests, key: str) -> str:
    copy = set_question(tests, "", questions_of(tests)[key])
    for row in tests.get("rows", []):
        row["answers"][copy] = row["answers"].get(key, "")
    return copy


def remove_question(tests: MethodTests, key: str) -> None:
    tests.get("questions", {}).pop(key, None)
    for row in tests.get("rows", []):
        row["answers"].pop(key, None)


def answers_to(tests: MethodTests, question: str) -> list[str]:
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


def answer_value_pairs(question: Question | None, answer: str) -> list[tuple[str, str]] | None:
    """Each value of an answer of several values, a list's items one by one, with what it is; None for an answer
    written whole or as one value."""
    if question is None or not answer or (schema := question.answer_schema()).get("type") != "object":
        return None
    values = question.answer_values(answer)
    if len(schema["properties"]) < 2 and not any(isinstance(value, list) for value in values.values()):
        return None
    return [
        (property_schema.get("title", name), "" if item is None else str(item))
        for name, property_schema in schema["properties"].items()
        for item in _as_list(values.get(name))
    ]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


@dataclass(frozen=True)
class TestAnswerView:
    """A test answer to one question as the answers column shows it, and what the last run gave it."""

    index: int
    answer: str
    is_example: bool
    result: QuestionResult | None
    marks: float | None
    form: ObjectForm | None = None
    """The answer's values to change one by one, for a question whose answers have them."""
    values: list[tuple[str, str]] | None = None
    """Each value of an answer of several values, with what it is."""

    @property
    def number(self) -> int:
        return self.index + 1


def answer_rows(
    tests: MethodTests, question: str, results: list[RowResult] | None, shown_question: Question | None = None
) -> list[TestAnswerView]:
    """Each row's test answer to `question`, with what the run of `results` gave it, if it marked these answers."""
    rows = tests.get("rows", [])
    row_results = results or []
    views = []
    for index, row in enumerate(rows):
        matches = index < len(row_results) and row_results[index].sheet.answers == row["answers"]
        result = row_results[index] if matches else None
        question_result = result.results.get(question) if result is not None else None
        marks = question_result.marks if question_result is not None else None
        answer = row["answers"].get(question, "")
        form = answer_form(shown_question, answer, f"answer{index}")
        pairs = answer_value_pairs(shown_question, answer)
        views.append(TestAnswerView(index, answer, row["is_example"], question_result, marks, form, pairs))
    return views
