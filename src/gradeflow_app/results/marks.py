"""Students' marks: each result, or the adjustment that replaces it, totalled per student and summarised per
question."""

import math
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cached_property
from typing import Literal, NamedTuple
from uuid import UUID

from gradeflow_core import CheckResult, Marked, QuestionResult, Submission

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.imports.models import Student
from gradeflow_app.questions.models import Question
from gradeflow_app.results.models import Adjustment, Result

MAX_BARS = 50
BAND_WIDTHS = (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500)

type Level = Literal["full", "partial", "no_marks"]


@dataclass
class Award:
    """What a student gets for a question: the adjustment a person set, else what the marking gives, else the
    submissions file's marks."""

    max_marks: float
    result: Result | None = None
    adjustment: Adjustment | None = None
    imported_marks: float | None = None
    preview_result: QuestionResult | None = None

    @property
    def marks(self) -> float | None:
        """From 0 to the question's maximum, as in the core's result."""
        marks = self.imported_marks
        if self.adjustment is not None and self.adjustment.marks is not None:
            marks = self.adjustment.marks
        elif (marking_result := self.marking_result) is not None:
            return marking_result.marks
        return None if marks is None else min(max(marks, 0.0), self.max_marks)

    @cached_property
    def marking_result(self) -> QuestionResult | None:
        """What the marking gives, or the draft's preview while previewing."""
        if self.preview_result is not None:
            return self.preview_result
        return self.result.question_result(self.max_marks) if self.result else None

    @property
    def is_previous_result(self) -> bool:
        return self.result is not None and (
            self.result.marking is None or self.result.revision != self.result.marking.revision
        )

    @property
    def marking_marks(self) -> float | None:
        """What the marking gives, beside an adjustment."""
        return self.result.question_result(self.max_marks).marks if self.result else None

    @property
    def feedback(self) -> str:
        if self.adjustment is not None and self.adjustment.feedback is not None:
            return self.adjustment.feedback
        return marking_result.feedback if (marking_result := self.marking_result) is not None else ""

    @property
    def breakdown(self) -> CheckResult | None:
        """How each check of the marking marked the answer; None for a marking of one check."""
        return marking_result.breakdown if (marking_result := self.marking_result) is not None else None

    def differs_from(self, other: Award) -> bool:
        """Whether `other`, such as this award with a preview's result, gives other marks or feedback."""
        return (self.marks, self.feedback) != (other.marks, other.feedback)

    def question_result(self) -> QuestionResult | None:
        """The award as the core's result; None without marks."""
        if (marks := self.marks) is None:
            return None
        marking_result = self.marking_result
        return QuestionResult(
            proportion=_proportion(marks, self.max_marks),
            max_marks=self.max_marks,
            feedback=self.feedback,
            method_label=marking_result.method_label if marking_result else "",
            is_marked=marking_result is None
            or marking_result.is_marked
            or (self.adjustment is not None and self.adjustment.marks is not None),
        )

    @property
    def level(self) -> Level | None:
        """Full, partial or no marks; None without marks."""
        return None if (marks := self.marks) is None else level_of(marks, marks, self.max_marks)


def level_of(lowest_marks: float, highest_marks: float, max_marks: float) -> Level:
    """Full, partial or no marks, for marks from `lowest_marks` to `highest_marks`. Full marks are the core's, which
    allow for rounding."""
    if QuestionResult(proportion=_proportion(lowest_marks, max_marks), max_marks=max_marks).correct:
        return "full"
    return "no_marks" if highest_marks == 0 else "partial"


def _proportion(marks: float, max_marks: float) -> float:
    return min(max(marks / max_marks, 0.0), 1.0) if max_marks else 0.0


def awards(questions: Sequence[Question], students: Sequence[Student]) -> dict[tuple[UUID, UUID], Award]:
    """Each student's award for each question, by student and question ID."""
    results = {
        (result.student_id, result.question_id): result
        for result in Result.objects.filter(question__in=questions, student__in=students).select_related("marking")
    }
    adjustments = {
        (adjustment.student_id, adjustment.question_id): adjustment
        for adjustment in Adjustment.objects.filter(question__in=questions, student__in=students)
    }
    return {
        (student.pk, question.pk): Award(
            question.core_question.max_marks,
            results.get((student.pk, question.pk)),
            adjustments.get((student.pk, question.pk)),
            student.imported_marks.get(question.key),
        )
        for student in students
        for question in questions
    }


@dataclass
class StudentMarks:
    student: Student
    awards: dict[str, Award]
    """By question key, for every question."""

    @property
    def total(self) -> float:
        return sum(award.marks for award in self.awards.values() if award.marks is not None)

    @property
    def adjusted_keys(self) -> list[str]:
        return [key for key, award in self.awards.items() if award.adjustment is not None]


@dataclass
class QuestionSummary:
    question: Question
    marked_count: int
    average: float | None
    full_marks_count: int
    no_marks_count: int
    blank_count: int
    adjusted_count: int

    @property
    def average_proportion(self) -> float | None:
        max_marks = self.question.core_question.max_marks
        return self.average / max_marks if self.average is not None and max_marks else None


@dataclass(frozen=True)
class TotalStatistics:
    """The spread of students' totals, over the whole class: quartiles as spreadsheets count them (inclusive), and the
    population standard deviation."""

    student_count: int
    mean: float
    standard_deviation: float
    minimum: float
    lower_quartile: float
    median: float
    upper_quartile: float
    maximum: float

    @classmethod
    def of(cls, totals: Sequence[float]) -> TotalStatistics | None:
        if not totals:
            return None
        quartiles = statistics.quantiles(totals, n=4, method="inclusive") if len(totals) > 1 else [totals[0]] * 3
        lower_quartile, median, upper_quartile = quartiles
        return cls(
            len(totals),
            statistics.fmean(totals),
            statistics.pstdev(totals),
            min(totals),
            lower_quartile,
            median,
            upper_quartile,
            max(totals),
        )


@dataclass
class Results:
    assessment: Assessment
    questions: list[Question]
    rows: list[StudentMarks]

    @property
    def max_marks(self) -> float:
        return sum(question.core_question.max_marks for question in self.questions)

    @property
    def totals(self) -> list[float]:
        return [row.total for row in self.rows]

    @property
    def statistics(self) -> TotalStatistics | None:
        return TotalStatistics.of(self.totals)

    def marked_submission(self, row: StudentMarks) -> Marked[Submission]:
        """The row's student as the core's marked submission, with results for the questions it has marks for."""
        results = {
            question.key: result
            for question in self.questions
            if (result := row.awards[question.key].question_result()) is not None
        }
        return Marked(sheet=row.student.submission, results=results, max_marks=self.max_marks)

    @property
    def has_marks(self) -> bool:
        return any(award.marks is not None for row in self.rows for award in row.awards.values())

    @cached_property
    def has_adjustments(self) -> bool:
        return any(row.adjusted_keys for row in self.rows)

    def summaries(self) -> list[QuestionSummary]:
        summaries = []
        for question in self.questions:
            awards = [row.awards[question.key] for row in self.rows]
            marks = [award.marks for award in awards if award.marks is not None]
            levels = Counter(award.level for award in awards)
            answers = Counter(row.student.answers.get(question.key, "") for row in self.rows)
            summaries.append(
                QuestionSummary(
                    question=question,
                    marked_count=len(marks),
                    average=statistics.fmean(marks) if marks else None,
                    full_marks_count=levels["full"],
                    no_marks_count=levels["no_marks"],
                    blank_count=sum(
                        count for answer, count in answers.items() if not question.core_question.normalize(answer)
                    ),
                    adjusted_count=sum(award.adjustment is not None for award in awards),
                )
            )
        return summaries


class Band(NamedTuple):
    """Totals from `start` to `end`; a bar per mark starts and ends at its mark."""

    start: float
    end: float
    students: int


def histogram(totals: Sequence[float], max_marks: float, narrower_steps: int = 0) -> list[Band]:
    """Students per band of totals. Bands take the round width nearest to what Rice's rule suggests for the class
    (2∛n bands), or `narrower_steps` round widths narrower (wider, when negative); whole marks when every total is
    whole: a width of 1 gives a bar per mark."""
    if not totals or not max_marks:
        return []
    all_whole = all(total.is_integer() for total in totals)
    suggested = max_marks / (2 * math.cbrt(len(totals)))
    widths = [width for width in BAND_WIDTHS if max_marks / width <= MAX_BARS and not (all_whole and width % 1)]
    nearest = min(range(len(widths)), key=lambda i: abs(math.log(widths[i] / suggested)))
    width = widths[min(max(nearest - narrower_steps, 0), len(widths) - 1)]
    if all_whole and width == 1:
        counts = Counter(int(total) for total in totals)
        return [Band(mark, mark, counts[mark]) for mark in range(int(max_marks) + 1)]
    band_count = math.ceil(max_marks / width)
    counts = Counter(min(int(total // width), band_count - 1) for total in totals)
    return [Band(i * width, min((i + 1) * width, max_marks), counts[i]) for i in range(band_count)]


def assessment_results(assessment: Assessment, students: Sequence[Student] | None = None) -> Results:
    """The marks of `students`, or else of every student."""
    questions = list(assessment.questions.all())
    students = list(assessment.students.all()) if students is None else students
    student_awards = awards(questions, students)
    rows = [
        StudentMarks(student, {question.key: student_awards[student.pk, question.pk] for question in questions})
        for student in students
    ]
    return Results(assessment, questions, rows)


def average_proportions(assessment: Assessment) -> dict[UUID, float]:
    """The average proportion of each question's marks, over the students with marks for it, by question ID: the
    summaries' alone, without reading the answers."""
    questions = list(assessment.questions.all())
    students = list(assessment.students.only("imported_marks"))
    student_awards = awards(questions, students)
    averages = {}
    for question in questions:
        marks = [
            student_marks
            for student in students
            if (student_marks := student_awards[student.pk, question.pk].marks) is not None
        ]
        if marks and question.core_question.max_marks:
            averages[question.pk] = statistics.fmean(marks) / question.core_question.max_marks
    return averages
