"""The guide to marking methods, written by the core: what each is for, how it works and worked examples."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from django.http import Http404
from django.utils.text import get_text_list
from gradeflow_core import GradeFlowError, Method, Methods, QuestionSet
from gradeflow_core.editing import Draft, Example, MethodDraft, Position

from gradeflow_app.libraries.collection import BUILTIN_NAMES, OfferedMethods
from gradeflow_app.questions.models import QUESTION_TYPE_LABELS


@dataclass(frozen=True)
class Section:
    label: str
    methods: list[tuple[Method, str]] = field(default_factory=list)
    """Each method, and who made it if someone did."""
    tab: str = "builtin"
    """The picker's tab it's under: the core's methods, the person's own and their courses', or public ones."""


PICKER_TABS = {"builtin": "Built-in", "yours": "Yours", "public": "Public"}


@dataclass(frozen=True)
class Choices:
    """The latest version of each method to choose from."""

    everyday: list[Method]
    transforms: list[Method]
    saved: list[Method]
    advanced: list[Method]

    def __len__(self) -> int:
        return len(self.everyday) + len(self.transforms) + len(self.saved) + len(self.advanced)


def choices(methods: Methods, offers: OfferedMethods) -> Choices:
    available = {method.name: method for method in methods.latest()}
    builtin = [method for name, method in available.items() if name in BUILTIN_NAMES]
    transforms = [method for method in builtin if not method.gives_marks({})]
    marking_methods = [method for method in builtin if method.gives_marks({})]
    everyday = [method for method in marking_methods if method.level == "basic"]
    advanced = [method for method in marking_methods if method.level != "basic"]
    if not everyday:
        everyday, advanced = advanced, []
    saved = [available[name] for _, names in offers.sections for name in names if name in available]
    return Choices(everyday, transforms, saved, advanced)


def sections_of(methods: Methods, offers: OfferedMethods) -> list[Section]:
    """The methods to choose from, in their tabs and sections."""
    newest = methods.latest()
    available = {method.name: method for method in newest}
    method_choices = choices(newest, offers)

    def with_owners(names: Iterable[str]) -> list[tuple[Method, str]]:
        return [(available[name], offers.owners[name]) for name in names if name in available]

    return [
        Section("", [(method, "") for method in method_choices.everyday]),
        Section("Transform the answer", [(method, "") for method in method_choices.transforms]),
        Section("Advanced", [(method, "") for method in method_choices.advanced]),
        *(Section(label, with_owners(names), "yours") for label, names in offers.sections),
        Section("Public", with_owners(offers.public), "public"),  # labelled only among search results
    ]


class QuestionChoice(NamedTuple):
    questions: list[str]
    """The questions it may read."""
    question: str | None
    """The one it reads; none for a method of several questions."""


def question_choice(draft: Draft, target: str, chosen_question: str | None = None) -> QuestionChoice | None:
    """The questions a step added at `target` of a question group may read, and the one it reads: `chosen_question`
    or the first; none if it is `""` and methods of several questions can go there. None for any other step."""
    position = draft.position(target)
    if not position.is_in_group or position.question is not None:
        return None
    keys = list(draft.questions)
    if chosen_question == "" and allows_group_methods(draft, target):
        return QuestionChoice(keys, None)
    return QuestionChoice(keys, chosen_question if chosen_question in keys else next(iter(keys), None))


def allows_group_methods(draft: Draft, target: str) -> bool:
    """Whether methods of several questions can go at `target`."""
    return any(method.marks_question_group for method in draft.methods(target, None))


def _labels(question_types: Iterable[str]) -> list[str]:
    return [label for question_type, label in QUESTION_TYPE_LABELS.items() if question_type in question_types]


def works_with(method: Method | MethodDraft) -> list[str]:
    """What the method marks, as labels: its question types, then question groups."""
    return _labels(method.question_types) + (["Question groups"] if method.marks_question_group else [])


def used_for(method: Method) -> str:
    """What the method marks, as a sentence: "For text and number questions and question groups"."""
    type_labels = [label.lower() for label in _labels(method.question_types)]
    questions = "all" if len(type_labels) == len(QUESTION_TYPE_LABELS) else get_text_list(type_labels, "and")
    marks = ([f"{questions} questions"] if type_labels else []) + (
        ["question groups"] if method.marks_question_group else []
    )
    return f"For {' and '.join(marks)}" if marks else ""


class ExpectedRow(NamedTuple):
    """Answers as an example declares them marked: the feedback, by question when there are several, and the marks."""

    answers: dict[str, str]
    feedback: list[tuple[str, str]]
    marks: float
    max_marks: float


def expected_rows(example: Example) -> list[ExpectedRow]:
    """The rows of `example` that declare a result."""
    return [
        ExpectedRow(
            row.answers,
            [
                (key if len(row.expected) > 1 else "", expected.feedback)
                for key, expected in row.expected.items()
                if expected.feedback
            ],
            sum(expected.marks for expected in row.expected.values()),
            sum(example.questions[key].max_marks for key in row.expected),
        )
        for row in example.rows
        if row.expected
    ]


def guide_context(
    ref: str, methods: Methods, question_type: str = "", owners: Mapping[str, str] = {}
) -> dict[str, Any]:
    """A method's guide, its examples that show a result with those rows, of marking questions of `question_type`
    first; for a saved method, who made it (from `owners`, by name) and what using it asks for."""
    try:
        method = methods.find(ref)
    except GradeFlowError:
        raise Http404 from None
    examples_with_rows = [(example, rows) for example in method.examples if (rows := expected_rows(example))]
    examples = sorted(
        examples_with_rows,
        key=lambda pair: question_type not in {question.type for question in pair[0].questions.values()},
    )
    is_saved_method = method.name not in BUILTIN_NAMES
    return {
        "method": method,
        "examples": examples,
        "used_for": used_for(method),
        "is_saved_method": is_saved_method,
        "owner": owners.get(method.name, ""),
        "setting_titles": _setting_titles(method) if is_saved_method else [],
    }


def _setting_titles(method: Method) -> list[str]:
    """What a step using the method is set up with, by title, in order."""
    schema = method.settings_schema(Position(QuestionSet({})))
    return [setting.get("title", name) for name, setting in schema.get("properties", {}).items()]
