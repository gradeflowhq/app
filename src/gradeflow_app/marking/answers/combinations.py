"""The answers column of a question group: students grouped by their answers to the questions it marks and reads."""

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from gradeflow_core import Question as CoreQuestion

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.marking.previews import PreviewResults
from gradeflow_app.results.marks import StudentMarks, awards


@dataclass
class Cell:
    """One question's answer in a combination, and the marks the students giving it have."""

    key: str
    answer: str
    question: CoreQuestion
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
    questions = [
        question for question in assessment.questions.filter(key__in=[*keys, *reads]) if not question.load_problems
    ]
    students = list(assessment.students.all())
    student_awards = awards(questions, students)
    return combinations_of(
        {question.key: question.core_question for question in questions},
        keys,
        [
            StudentMarks(student, {question.key: student_awards[student.pk, question.pk] for question in questions})
            for student in students
        ],
        results,
    )


def combinations_of(
    questions: Mapping[str, CoreQuestion],
    keys: Collection[str],
    students: Iterable[StudentMarks],
    results: PreviewResults | None,
) -> list[Combination]:
    """Every combination of answers to `questions` that `students` gave, most common first. `keys` are the questions
    marked; the others are only read. `questions` are in the assessment's order, so a condition reads before what it
    decides."""
    by_answers: dict[tuple[str, ...], Combination] = {}
    for student in students:
        answers = tuple(question.normalize(student.student.answers.get(key, "")) for key, question in questions.items())
        combination = by_answers.get(answers)
        if combination is None:
            cells = [
                Cell(key, answer, question, read=key not in keys)
                for (key, question), answer in zip(questions.items(), answers, strict=True)
            ]
            combination = by_answers[answers] = Combination(cells)
        combination.student_count += 1
        preview_results = (results or {}).get(student.student.encrypted_id, {})
        changed = False
        unmarked = False
        for cell in combination.cells:
            if cell.read:
                continue
            award = student.awards[cell.key]
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
