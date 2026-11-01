"""The guide to marking methods, written by the core: what each is for, how it works and worked examples."""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol

from django.http import Http404
from gradeflow_core import Method, Methods
from gradeflow_core.editing import Example, MarkingDraft, QuestionType

from gradeflow_app.libraries.collection import BUILTIN_NAMES, Offers
from gradeflow_app.questions.models import TYPE_LABELS


@dataclass(frozen=True)
class Section:
    label: str
    entries: list[tuple[Method[Any], str]] = field(default_factory=list)
    """Each method, and who made it if someone did."""
    tab: str = "builtin"
    """The picker's tab it's under: the core's methods, the person's own and their courses', or public ones."""


TABS = {"builtin": "Built-in", "yours": "Yours", "public": "Public"}


@dataclass(frozen=True)
class Choices:
    """The latest version of each method to choose from: the everyday built-in ones, the person's saved ones, then
    the advanced ones under "Advanced" if there are everyday ones too."""

    methods: list[Method[Any]]
    saved: list[Method[Any]]
    more: list[Method[Any]]

    def __len__(self) -> int:
        return len(self.methods) + len(self.saved) + len(self.more)


def choices(methods: Methods, offers: Offers) -> Choices:
    available = {method.name: method for method in methods.latest()}
    builtin = [method for name, method in available.items() if name in BUILTIN_NAMES]
    everyday = [method for method in builtin if method.level == "basic"]
    more = [method for method in builtin if method.level != "basic"]
    if not everyday:
        everyday, more = more, []
    saved = [available[name] for _, names in offers.sections for name in names if name in available]
    return Choices(everyday, saved, more)


def sections_of(methods: Methods, offers: Offers) -> list[Section]:
    """The methods to choose from, in their tabs and sections."""
    newest = methods.latest()
    available = {method.name: method for method in newest}
    split = choices(newest, offers)

    def entries(names: Iterable[str]) -> list[tuple[Method[Any], str]]:
        return [(available[name], offers.owners[name]) for name in names if name in available]

    return [
        Section("", [(method, "") for method in split.methods]),
        Section("Advanced", [(method, "") for method in split.more]),
        *(Section(label, entries(names), "yours") for label, names in offers.sections),
        Section("Public", entries(offers.public), "public"),  # labelled only among search results
    ]


class QuestionGroupCheck(NamedTuple):
    questions: list[str]
    """The questions it may read."""
    question: str | None
    """The one it reads; none for a method of several questions."""


def question_group_check(checks: MarkingDraft, target: str, asked: str | None = None) -> QuestionGroupCheck | None:
    """For a check added at `target` of a question group, which names the question it reads: the question asked
    for, by default the first, or none, asked as `""`, where methods of several questions can go. None for any
    other check."""
    position = checks.position(target)
    if not position.in_group or position.question is not None:
        return None
    keys = list(checks.questions)
    if asked == "" and several(checks, target):
        return QuestionGroupCheck(keys, None)
    return QuestionGroupCheck(keys, asked if asked in keys else next(iter(keys), None))


def several(checks: MarkingDraft, target: str) -> bool:
    """Whether methods of several questions can go at `target`."""
    return any(method.group for method in checks.methods(target, None))


class Marks(Protocol):
    """What a method, published or a draft, marks."""

    @property
    def question_types(self) -> frozenset[QuestionType]: ...

    @property
    def group(self) -> bool: ...


def _labels(kinds: Iterable[str]) -> list[str]:
    return [label for kind, label in TYPE_LABELS.items() if kind in kinds]


def works_with(method: Marks) -> list[str]:
    """What the method marks, as labels: its question types, then question groups."""
    return _labels(method.question_types) + (["Question groups"] if method.group else [])


def listed(items: Sequence[str]) -> str:
    """Items in a sentence: "a", "a and b", "a, b and c"."""
    return " and ".join(filter(None, [", ".join(items[:-1]), *items[-1:]]))


def used_for(method: Method[Any]) -> str:
    """What the method marks, as a sentence: "For text and number questions and question groups"."""
    kinds = [label.lower() for label in _labels(method.question_types)]
    questions = "all" if len(kinds) == len(TYPE_LABELS) else listed(kinds)
    marks = ([f"{questions} questions"] if kinds else []) + (["question groups"] if method.group else [])
    return f"For {' and '.join(marks)}" if marks else ""


class ShownRow(NamedTuple):
    """Answers as an example declares them marked: the feedback, by question when there are several, and the marks."""

    answers: dict[str, str]
    feedback: list[tuple[str, str]]
    marks: float
    max_marks: float


def shown_rows(example: Example) -> list[ShownRow]:
    """The rows of `example` that declare a result."""
    return [
        ShownRow(
            row.answers,
            [
                (key if len(row.expected) > 1 else "", said.feedback)
                for key, said in row.expected.items()
                if said.feedback
            ],
            sum(said.marks for said in row.expected.values()),
            sum(example.questions[key].max_marks for key in row.expected),
        )
        for row in example.rows
        if row.expected
    ]


def guide_context(ref: str, methods: Methods, kind: str = "", owners: Mapping[str, str] = {}) -> dict[str, Any]:
    """A method's guide, its examples that show a result with those rows, of marking questions of type `kind` first;
    for a saved method, who made it (from `owners`, by name) and what using it asks for."""
    guide = methods.by_ref.get(ref)
    if guide is None:
        raise Http404
    shown = [(example, rows) for example in guide.examples if (rows := shown_rows(example))]
    examples = sorted(shown, key=lambda shown: kind not in {q.type for q in shown[0].questions.values()})
    saved = guide.name not in BUILTIN_NAMES
    return {
        "guide": guide,
        "examples": examples,
        "used_for": used_for(guide),
        "saved": saved,
        "owner": owners.get(guide.name, ""),
        "asks": guide.setting_titles if saved else [],
    }
