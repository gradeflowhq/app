"""The answers column of a question group: students grouped by their answers to the questions it marks and reads."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.previews import Previewed
from gradeflow_app.results.marks import awards


@dataclass
class Cell:
    """One question's answer in a combination, and the marks the students giving it have."""

    key: str
    answer: str
    max_marks: float
    read: bool = False
    """Whether the marking only reads this question's answer, as a condition does, rather than marking it."""
    marks: set[float | None] = field(default_factory=set)
    preview: set[float] = field(default_factory=set)
    feedbacks: Counter[str] = field(default_factory=Counter)
    previewed: Counter[str] = field(default_factory=Counter)
    changes: int = 0
    problem: str = ""
    """Why the preview couldn't mark this answer, if it couldn't."""

    @property
    def feedback(self) -> str:
        """The most common feedback, the preview's while previewing."""
        commonest = (self.previewed or self.feedbacks).most_common(1)
        return commonest[0][0] if commonest else ""

    @property
    def low(self) -> float | None:
        return min((value for value in self.marks if value is not None), default=None)

    @property
    def high(self) -> float | None:
        return max((value for value in self.marks if value is not None), default=None)

    @property
    def new_low(self) -> float | None:
        return min(self.preview, default=None)

    @property
    def new_high(self) -> float | None:
        return max(self.preview, default=None)

    @property
    def changed(self) -> bool:
        return self.changes > 0


@dataclass
class Combination:
    cells: list[Cell]
    count: int = 0
    changes: int = 0
    unmarked: int = 0

    @property
    def changed(self) -> bool:
        return self.changes > 0


def combinations(
    assessment: Assessment, keys: list[str], results: Previewed | None, reads: Sequence[str] = ()
) -> list[Combination]:
    """Every combination of answers to `keys`, and to the questions in `reads`, that students gave, most common
    first."""
    questions = {question.key: question for question in assessment.questions.filter(key__in=[*keys, *reads])}
    ordered_keys = list(questions)  # in the exam's order, so a condition reads before what it decides
    students = list(assessment.students.all())
    awarded = awards(list(questions.values()), students)
    rows: dict[tuple[str, ...], Combination] = {}
    for student in students:
        given = tuple(questions[key].core.normalize(student.answers.get(key, "")) for key in ordered_keys)
        row = rows.get(given)
        if row is None:
            cells = [
                Cell(k, a, questions[k].core.max_marks, read=k not in keys)
                for k, a in zip(ordered_keys, given, strict=True)
            ]
            row = rows[given] = Combination(cells)
        row.count += 1
        previewed = (results or {}).get(student.key, {})
        changed = False
        unmarked = False
        for cell in row.cells:
            if cell.read:
                continue
            award = awarded[student.pk, questions[cell.key].pk]
            cell.marks.add(award.marks)
            cell.feedbacks[award.feedback] += 1
            if (result := previewed.get(cell.key)) is not None:
                projected = award.with_projected(result)
                assert projected.marks is not None
                cell.preview.add(projected.marks)
                cell.previewed[projected.feedback] += 1
                differs = award.differs_from(projected)
                cell.changes += differs
                changed |= differs
                if not result.marked:
                    cell.problem = cell.problem or result.feedback
                    unmarked = True
        row.changes += changed
        row.unmarked += unmarked
    return sorted(rows.values(), key=lambda row: -row.count)
