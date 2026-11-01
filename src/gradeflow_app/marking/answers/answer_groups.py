"""Answer groups: students' answers to a question, grouped so a marker reviews each distinct answer once."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal

from gradeflow_core import AnswerComparison, compare_answers

from gradeflow_app.imports.models import answer_to
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import Award, Level, awards

type By = Literal["answer", "feedback"]
type Similar = Literal["exact", "normalized", "spelling", "meaning"]

MAX_COMPARED = 1000
"""Above this many normalized answers, the page groups only equal answers."""


@dataclass(frozen=True)
class Options:
    by: By = "answer"
    similar: Similar = "exact"
    threshold: float = 0.85
    """How similar answers must be to group, until someone moves the slider."""


@dataclass(kw_only=True)
class Entry(Award):
    student: str
    """The student's row ID."""
    key: str
    """The student ID's envelope."""
    answer: str
    canonical: str
    """The answer as the question reads it, so `42` and `42.0`, or `B,D` and `D,B`, group together; blank is `""`."""


@dataclass(frozen=True)
class Preview:
    """The marks a draft marking gives an answer group's answers."""

    low: float
    high: float
    feedback: str
    changes: int
    """How many of its students' marks change."""
    unmarked: int
    """How many of its students the draft couldn't mark."""
    all_unmarked: bool
    """Whether it couldn't mark any of them."""
    problem: str
    """Why the draft couldn't mark the first of them."""
    full: int = 0
    part: int = 0
    zero: int = 0

    @property
    def changed(self) -> bool:
        return self.changes > 0

    @property
    def levels(self) -> set[Level]:
        """The levels of the marks it gives."""
        counts: dict[Level, int] = {"full": self.full, "part": self.part, "zero": self.zero}
        return {level for level, count in counts.items() if count}


@dataclass
class AnswerGroup:
    key: str
    entries: list[Entry] = field(default_factory=list)
    preview: Preview | None = None
    """The draft marking's marks for the answer group, while previewing."""
    decisions: list[tuple[str, str]] = field(default_factory=list)
    """The decisions marking by example gave its answers, each as its label and badge style."""
    mixed_decisions: bool = False
    approximate: bool = False
    """Different answers joined by similarity; review them separately before changing marks."""

    @property
    def answers(self) -> list[tuple[str, int]]:
        return Counter(entry.answer for entry in self.entries).most_common()

    @property
    def answer(self) -> str:
        return Counter(entry.canonical for entry in self.entries).most_common(1)[0][0]

    @property
    def count(self) -> int:
        return len(self.entries)

    @property
    def marks(self) -> tuple[float | None, float | None]:
        values = [entry.marks for entry in self.entries if entry.marks is not None]
        return (min(values), max(values)) if values else (None, None)

    @property
    def feedback(self) -> str:
        commonest = Counter(entry.feedback for entry in self.entries if entry.feedback).most_common(1)
        return commonest[0][0] if commonest else ""

    @property
    def levels(self) -> set[Level]:
        """The levels of the marks its students have, as shown: the previewed ones while previewing."""
        if self.preview is not None:
            return self.preview.levels
        return {level for entry in self.entries if (level := entry.level) is not None}

    @property
    def adjusted(self) -> int:
        return sum(entry.adjustment is not None for entry in self.entries)

    @property
    def unmarked(self) -> int:
        """How many of its students the marking couldn't mark, such as answers it couldn't read."""
        return sum(entry.result is not None and not entry.result.marked for entry in self.entries)

    @property
    def all_unmarked(self) -> bool:
        """Whether the marking couldn't mark any of its students."""
        return self.unmarked == self.count

    @property
    def blank(self) -> bool:
        return not self.answer

    @property
    def students(self) -> list[str]:
        return [entry.student for entry in self.entries]


def entries(question: Question) -> list[Entry]:
    students = list(question.assessment.students.annotate(answer=answer_to(question.key)))
    awarded = awards([question], students)
    canonical: dict[str, str] = {}
    answered = []
    for student in students:
        answer: str = student.answer
        if answer not in canonical:
            canonical[answer] = question.core.normalize(answer)
        award = awarded[student.pk, question.pk]
        answered.append(
            Entry(
                award.max_marks,
                award.result,
                award.adjustment,
                award.file_marks,
                student=str(student.pk),
                key=student.key,
                answer=answer,
                canonical=canonical[answer],
            )
        )
    return answered


def text_of(entry: Entry, options: Options) -> str:
    if options.by == "feedback" and entry.feedback:
        return entry.feedback
    return entry.canonical


def texts_of(entries: Sequence[Entry], options: Options) -> list[str]:
    return sorted({text_of(entry, options) for entry in entries})


def too_many(texts: Sequence[str]) -> bool:
    return len(compare_answers(texts, similar="normalized").groups()) > MAX_COMPARED


def group(entries: Sequence[Entry], options: Options, comparison: AnswerComparison | None = None) -> list[AnswerGroup]:
    """Attach student rows to core's groups, then order the largest groups first. Similar meaning needs `comparison`;
    without it, only equal answers group."""
    if options.by == "feedback":
        # Answers without feedback retain their question's exact reading rules.
        with_feedback = [entry for entry in entries if entry.feedback]
        without_feedback = [entry for entry in entries if not entry.feedback]
        groups = _groups(with_feedback, options, "normalized") + _groups(without_feedback, Options(), "exact")
    else:
        similar: Similar = "normalized" if options.similar == "meaning" and comparison is None else options.similar
        groups = _groups(entries, options, similar, comparison)
    return sorted(groups, key=lambda g: (-g.count, g.key))


def _groups(
    entries: Sequence[Entry], options: Options, similar: Similar, comparison: AnswerComparison | None = None
) -> list[AnswerGroup]:
    buckets: dict[str, list[Entry]] = {}
    for entry in entries:
        buckets.setdefault(text_of(entry, options), []).append(entry)
    texts = sorted(buckets)
    prepared = comparison if comparison is not None else compare_answers(texts, similar=similar)
    groups = []
    for indices in prepared.groups(threshold=options.threshold):
        grouped = AnswerGroup("", entries=[entry for index in indices for entry in buckets[texts[index]]])
        grouped.key = grouped.answer
        grouped.approximate = options.similar != "exact" and len({entry.canonical for entry in grouped.entries}) > 1
        groups.append(grouped)
    return groups
