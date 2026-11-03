"""The answers column of a question group: students grouped by their answers to the questions it marks and reads."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.previews import PreviewResults
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
    preview_marks: set[float] = field(default_factory=set)
    feedback_counts: Counter[str] = field(default_factory=Counter)
    preview_feedback_counts: Counter[str] = field(default_factory=Counter)
    changed_count: int = 0
    problem: str = ""
    """Why the preview couldn't mark this answer, if it couldn't."""

    @property
    def feedback(self) -> str:
        """The most common feedback, the preview's while previewing."""
        commonest = (self.preview_feedback_counts or self.feedback_counts).most_common(1)
        return commonest[0][0] if commonest else ""

    @property
    def lowest_marks(self) -> float | None:
        return min((value for value in self.marks if value is not None), default=None)

    @property
    def highest_marks(self) -> float | None:
        return max((value for value in self.marks if value is not None), default=None)

    @property
    def preview_lowest_marks(self) -> float | None:
        return min(self.preview_marks, default=None)

    @property
    def preview_highest_marks(self) -> float | None:
        return max(self.preview_marks, default=None)

    @property
    def changed(self) -> bool:
        return self.changed_count > 0


@dataclass
class Combination:
    cells: list[Cell]
    student_count: int = 0
    changed_count: int = 0
    unmarked_count: int = 0

    @property
    def changed(self) -> bool:
        return self.changed_count > 0


def combinations(
    assessment: Assessment, keys: list[str], results: PreviewResults | None, reads: Sequence[str] = ()
) -> list[Combination]:
    """Every combination of answers to `keys`, and to the questions in `reads`, that students gave, most common
    first."""
    questions = {question.key: question for question in assessment.questions.filter(key__in=[*keys, *reads])}
    ordered_keys = list(questions)  # in the exam's order, so a condition reads before what it decides
    students = list(assessment.students.all())
    student_awards = awards(list(questions.values()), students)
    by_answers: dict[tuple[str, ...], Combination] = {}
    for student in students:
        answers = tuple(questions[key].core_question.normalize(student.answers.get(key, "")) for key in ordered_keys)
        combination = by_answers.get(answers)
        if combination is None:
            cells = [
                Cell(key, answer, questions[key].core_question.max_marks, read=key not in keys)
                for key, answer in zip(ordered_keys, answers, strict=True)
            ]
            combination = by_answers[answers] = Combination(cells)
        combination.student_count += 1
        preview_results = (results or {}).get(student.encrypted_id, {})
        changed = False
        unmarked = False
        for cell in combination.cells:
            if cell.read:
                continue
            award = student_awards[student.pk, questions[cell.key].pk]
            cell.marks.add(award.marks)
            cell.feedback_counts[award.feedback] += 1
            if (result := preview_results.get(cell.key)) is not None:
                preview_award = replace(award, preview_result=result)
                assert preview_award.marks is not None
                cell.preview_marks.add(preview_award.marks)
                cell.preview_feedback_counts[preview_award.feedback] += 1
                differs = award.differs_from(preview_award)
                cell.changed_count += differs
                changed |= differs
                if not result.is_marked:
                    cell.problem = cell.problem or result.feedback
                    unmarked = True
        combination.changed_count += changed
        combination.unmarked_count += unmarked
    return sorted(by_answers.values(), key=lambda combination: -combination.student_count)
