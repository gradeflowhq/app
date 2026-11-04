"""Answer groups: students' answers to a question, grouped so a marker reviews each distinct answer once."""

import functools
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from gradeflow_core import AnswerComparison, CheckResult, compare_answers

from gradeflow_app.imports.models import answer_expression
from gradeflow_app.questions.models import Question
from gradeflow_app.results.marks import Award, Level, awards

type GroupBy = Literal["answer", "feedback"]
type Similarity = Literal["exact", "normalized", "spelling", "meaning"]

MAX_COMPARED = 1000
"""Above this many normalized answers, the page groups only equal answers."""


@dataclass(frozen=True)
class GroupingOptions:
    group_by: GroupBy = "answer"
    similarity: Similarity = "exact"
    threshold: float = 0.85
    """How similar answers must be to group, until someone moves the slider."""


@dataclass(kw_only=True)
class StudentAnswer(Award):
    student_pk: str
    encrypted_student_id: str
    answer: str
    normalized: str
    """The answer as the question reads it, so `42` and `42.0`, or `B,D` and `D,B`, group together; blank is `""`."""


@dataclass(frozen=True)
class Preview:
    """The marks a draft marking gives an answer group's answers."""

    lowest_marks: float
    highest_marks: float
    feedback: str
    changed_count: int
    """How many of its students' marks change."""
    unmarked_count: int
    """How many of its students the draft couldn't mark."""
    all_unmarked: bool
    """Whether it couldn't mark any of them."""
    problem: str
    """Why the draft couldn't mark the first of them."""
    breakdown: GroupBreakdown | None = None
    full_marks_count: int = 0
    partial_marks_count: int = 0
    no_marks_count: int = 0

    @property
    def changed(self) -> bool:
        return self.changed_count > 0

    @property
    def levels(self) -> set[Level]:
        """The levels of the marks it gives."""
        counts: dict[Level, int] = {
            "full": self.full_marks_count,
            "partial": self.partial_marks_count,
            "no_marks": self.no_marks_count,
        }
        return {level for level, count in counts.items() if count}


@dataclass(frozen=True)
class GroupBreakdown:
    """The breakdown most of an answer group's students have, and how many: a marking that reads other questions can
    mark the same answer differently."""

    breakdown: CheckResult
    student_count: int
    group_size: int

    @property
    def note(self) -> str:
        if self.student_count < self.group_size:
            return f"For {self.student_count} of {self.group_size} students"
        return ""


def commonest_breakdown(awards: Iterable[Award]) -> GroupBreakdown | None:
    """The breakdown most of ``awards`` have; None if none has one."""
    breakdowns = [award.breakdown for award in awards]
    counts = Counter(breakdown for breakdown in breakdowns if breakdown is not None)
    if not counts:
        return None
    [(breakdown, student_count)] = counts.most_common(1)
    return GroupBreakdown(breakdown, student_count, len(breakdowns))


@dataclass
class AnswerGroup:
    student_answers: list[StudentAnswer] = field(default_factory=list)
    preview: Preview | None = None
    """The draft marking's marks for the answer group, while previewing."""
    decisions: list[tuple[str, str]] = field(default_factory=list)
    """The decisions marking by example gave its answers, each as its label and badge style."""
    has_mixed_decisions: bool = False
    joined_by_similarity: bool = False
    """Different answers joined by similarity; review them separately before changing marks."""

    @property
    def answers(self) -> list[tuple[str, int]]:
        return Counter(entry.answer for entry in self.student_answers).most_common()

    @property
    def normalized_answers(self) -> list[tuple[str, int]]:
        """Its answers as the question reads them, such as `42` for `42.0`."""
        return Counter(entry.normalized for entry in self.student_answers).most_common()

    @property
    def original_answers(self) -> list[tuple[str, int]]:
        """Its answers the question reads visibly differently from how the submissions file has them."""
        return Counter(
            entry.answer
            for entry in self.student_answers
            if _without_invisible_whitespace(entry.answer) != _without_invisible_whitespace(entry.normalized)
        ).most_common()

    @property
    def answer(self) -> str:
        return self.normalized_answers[0][0]

    @property
    def student_count(self) -> int:
        return len(self.student_answers)

    @property
    def marks_range(self) -> tuple[float | None, float | None]:
        values = [entry.marks for entry in self.student_answers if entry.marks is not None]
        return (min(values), max(values)) if values else (None, None)

    @property
    def feedback(self) -> str:
        commonest = Counter(entry.feedback for entry in self.student_answers if entry.feedback).most_common(1)
        return commonest[0][0] if commonest else ""

    @property
    def breakdown(self) -> GroupBreakdown | None:
        """Its students' commonest breakdown: the draft's while previewing."""
        if self.preview is not None:
            return self.preview.breakdown
        return commonest_breakdown(entry for entry in self.student_answers if entry.result is not None)

    @property
    def levels(self) -> set[Level]:
        """The levels of the marks its students have, as shown: the previewed ones while previewing."""
        if self.preview is not None:
            return self.preview.levels
        return {level for entry in self.student_answers if (level := entry.level) is not None}

    @property
    def adjusted_count(self) -> int:
        return sum(entry.adjustment is not None for entry in self.student_answers)

    @property
    def unmarked_count(self) -> int:
        """How many of its students the marking couldn't mark, such as answers it couldn't read."""
        return sum(entry.result is not None and not entry.result.is_marked for entry in self.student_answers)

    @property
    def all_unmarked(self) -> bool:
        """Whether the marking couldn't mark any of its students."""
        return self.unmarked_count == self.student_count

    @property
    def is_blank(self) -> bool:
        return not self.answer

    @property
    def student_pks(self) -> list[str]:
        return [entry.student_pk for entry in self.student_answers]


def _without_invisible_whitespace(text: str) -> str:
    """``text`` without whitespace no one sees: at the ends of lines, and blank lines around it."""
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def student_answers(question: Question) -> list[StudentAnswer]:
    students = list(question.assessment.students.annotate(answer=answer_expression(question.key)))
    student_awards = awards([question], students)
    normalize = functools.cache(question.core_question.normalize)
    rows = []
    for student in students:
        answer: str = student.answer
        award = student_awards[student.pk, question.pk]
        rows.append(
            StudentAnswer(
                award.max_marks,
                award.result,
                award.adjustment,
                award.imported_marks,
                student_pk=str(student.pk),
                encrypted_student_id=student.encrypted_id,
                answer=answer,
                normalized=normalize(answer),
            )
        )
    return rows


def grouping_text(entry: StudentAnswer, options: GroupingOptions) -> str:
    if options.group_by == "feedback" and entry.feedback:
        return entry.feedback
    return entry.normalized


def grouping_texts(student_answers: Sequence[StudentAnswer], options: GroupingOptions) -> list[str]:
    return sorted({grouping_text(entry, options) for entry in student_answers})


def too_many_to_compare(texts: Sequence[str]) -> bool:
    return len(compare_answers(texts, similarity="normalized").groups()) > MAX_COMPARED


def group(
    student_answers: Sequence[StudentAnswer], options: GroupingOptions, comparison: AnswerComparison | None = None
) -> list[AnswerGroup]:
    """Attach student rows to core's groups, then order the largest groups first. Similar meaning needs `comparison`;
    without it, only equal answers group."""
    if options.group_by == "feedback":
        # Answers without feedback retain their question's exact reading rules.
        with_feedback = [entry for entry in student_answers if entry.feedback]
        without_feedback = [entry for entry in student_answers if not entry.feedback]
        groups = _groups(with_feedback, options, "normalized") + _groups(without_feedback, GroupingOptions(), "exact")
    else:
        similarity: Similarity = (
            "normalized" if options.similarity == "meaning" and comparison is None else options.similarity
        )
        groups = _groups(student_answers, options, similarity, comparison)
    return sorted(groups, key=lambda group: (-group.student_count, group.answer))


def _groups(
    student_answers: Sequence[StudentAnswer],
    options: GroupingOptions,
    similarity: Similarity,
    comparison: AnswerComparison | None = None,
) -> list[AnswerGroup]:
    answers_by_text: dict[str, list[StudentAnswer]] = {}
    for entry in student_answers:
        answers_by_text.setdefault(grouping_text(entry, options), []).append(entry)
    texts = sorted(answers_by_text)
    if comparison is None:
        comparison = compare_answers(texts, similarity=similarity)
    groups = []
    for indices in comparison.groups(threshold=options.threshold):
        group = AnswerGroup([entry for index in indices for entry in answers_by_text[texts[index]]])
        normalized = {entry.normalized for entry in group.student_answers}
        group.joined_by_similarity = options.similarity != "exact" and len(normalized) > 1
        groups.append(group)
    return groups
