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

from gradeflow_core import CheckResult, MarkedSubmission, QuestionResult

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.imports.models import Student
from gradeflow_app.questions.models import Question
from gradeflow_app.results.models import Adjustment, Result

MAX_BARS = 50
BAND_WIDTHS = (0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500)

type Level = Literal["full", "part", "zero"]


@dataclass
class Award:
    """What a student gets for a question: the adjustment a person set, else what the marking gives, else the exam
    export's marks."""

    max_marks: float
    result: Result | None = None
    adjustment: Adjustment | None = None
    file_marks: float | None = None
    projected: QuestionResult | None = None

    @property
    def marks(self) -> float | None:
        if self.adjustment is not None and self.adjustment.marks is not None:
            return self.adjustment.marks
        if (given := self.given) is not None:
            return given.marks
        return self.file_marks

    @cached_property
    def given(self) -> QuestionResult | None:
        """What the marking gives, or the draft's preview while previewing."""
        if self.projected is not None:
            return self.projected
        return self.result.core(self.max_marks) if self.result else None

    @property
    def stale(self) -> bool:
        return self.result is not None and (
            self.result.marking is None or self.result.revision != self.result.marking.revision
        )

    @property
    def marking_marks(self) -> float | None:
        """What the marking gives, beside an adjustment."""
        return self.result.core(self.max_marks).marks if self.result else None

    @property
    def feedback(self) -> str:
        if self.adjustment is not None and self.adjustment.feedback is not None:
            return self.adjustment.feedback
        return given.feedback if (given := self.given) is not None else ""

    @property
    def breakdown(self) -> CheckResult | None:
        """How each check of the marking marked the answer; None for a marking of one check."""
        return given.breakdown if (given := self.given) is not None else None

    def with_projected(self, projected: QuestionResult) -> Award:
        return Award(self.max_marks, self.result, self.adjustment, self.file_marks, projected)

    def differs_from(self, other: Award) -> bool:
        """Whether `other`, such as this award with a preview's result projected, gives other marks or feedback."""
        return (self.marks, self.feedback) != (other.marks, other.feedback)

    def question_result(self) -> QuestionResult | None:
        """The award as the core's result; None without marks."""
        if (marks := self.marks) is None:
            return None
        given = self.given
        return QuestionResult(
            proportion=_proportion(marks, self.max_marks),
            max_marks=self.max_marks,
            feedback=self.feedback,
            method=given.method if given else "",
            marked=given is None or given.marked or (self.adjustment is not None and self.adjustment.marks is not None),
        )

    @property
    def level(self) -> Level | None:
        """Full, part or no marks; None without marks."""
        return None if (marks := self.marks) is None else level_of(marks, marks, self.max_marks)


def level_of(low: float, high: float, max_marks: float) -> Level:
    """Full, part or no marks, for marks from `low` to `high`. Full marks are the core's, which allow for rounding."""
    if QuestionResult(proportion=_proportion(low, max_marks), max_marks=max_marks).correct:
        return "full"
    return "zero" if high == 0 else "part"


def _proportion(marks: float, max_marks: float) -> float:
    return min(max(marks / max_marks, 0.0), 1.0) if max_marks else 0.0


def awards(questions: Sequence[Question], students: Sequence[Student]) -> dict[tuple[UUID, UUID], Award]:
    """Each student's award for each question, by student and question ID."""
    results = {
        (result.student_id, result.question_id): result
        for result in Result.objects.filter(question__in=questions).select_related("marking")
    }
    adjusted = {
        (adjustment.student_id, adjustment.question_id): adjustment
        for adjustment in Adjustment.objects.filter(question__in=questions, student__in=students)
    }
    return {
        (student.pk, question.pk): Award(
            question.core.max_marks,
            results.get((student.pk, question.pk)),
            adjusted.get((student.pk, question.pk)),
            student.file_marks.get(question.key),
        )
        for student in students
        for question in questions
    }


@dataclass
class Row:
    student: Student
    awards: dict[str, Award]
    """By question key, for every question."""

    @property
    def total(self) -> float:
        return sum(award.marks for award in self.awards.values() if award.marks is not None)

    @property
    def adjusted(self) -> list[str]:
        return [key for key, award in self.awards.items() if award.adjustment is not None]


@dataclass
class QuestionSummary:
    question: Question
    marked: int
    average: float | None
    full: int
    zero: int
    blank: int
    adjusted: int

    @property
    def average_proportion(self) -> float | None:
        max_marks = self.question.core.max_marks
        return self.average / max_marks if self.average is not None and max_marks else None


@dataclass(frozen=True)
class Stats:
    """The spread of students' totals, over the whole class: quartiles as spreadsheets count them (inclusive), and the
    population standard deviation."""

    students: int
    mean: float
    stdev: float
    minimum: float
    q1: float
    median: float
    q3: float
    maximum: float

    @classmethod
    def of(cls, totals: Sequence[float]) -> Stats | None:
        if not totals:
            return None
        quartiles = statistics.quantiles(totals, n=4, method="inclusive") if len(totals) > 1 else [totals[0]] * 3
        q1, median, q3 = quartiles
        return cls(
            len(totals), statistics.fmean(totals), statistics.pstdev(totals), min(totals), q1, median, q3, max(totals)
        )


@dataclass
class Results:
    assessment: Assessment
    questions: list[Question]
    rows: list[Row]

    @property
    def max_marks(self) -> float:
        return sum(question.core.max_marks for question in self.questions)

    @property
    def totals(self) -> list[float]:
        return [row.total for row in self.rows]

    @property
    def stats(self) -> Stats | None:
        return Stats.of(self.totals)

    def marked(self, row: Row) -> MarkedSubmission:
        """The row's student as the core's marked submission, with results for the questions it has marks for."""
        results = {
            question.key: result
            for question in self.questions
            if (result := row.awards[question.key].question_result()) is not None
        }
        return MarkedSubmission(**row.student.submission.model_dump(), results=results)

    @property
    def any_marks(self) -> bool:
        return any(award.marks is not None for row in self.rows for award in row.awards.values())

    @property
    def any_adjusted(self) -> bool:
        return any(row.adjusted for row in self.rows)

    def summaries(self) -> list[QuestionSummary]:
        summaries = []
        for question in self.questions:
            awarded = [row.awards[question.key] for row in self.rows]
            marked = [award.marks for award in awarded if award.marks is not None]
            levels = Counter(award.level for award in awarded)
            answers = Counter(row.student.answers.get(question.key, "") for row in self.rows)
            summaries.append(
                QuestionSummary(
                    question=question,
                    marked=len(marked),
                    average=statistics.fmean(marked) if marked else None,
                    full=levels["full"],
                    zero=levels["zero"],
                    blank=sum(count for answer, count in answers.items() if not question.core.normalize(answer)),
                    adjusted=sum(award.adjustment is not None for award in awarded),
                )
            )
        return summaries


class Band(NamedTuple):
    """Totals from `start` to `end`; a bar per mark starts and ends at its mark."""

    start: float
    end: float
    students: int


def histogram(totals: Sequence[float], max_marks: float, finer: int = 0) -> list[Band]:
    """Students per band of totals. Bands take the round width nearest to what Rice's rule suggests for the class
    (2∛n bands), or `finer` round widths narrower (wider, when negative); whole marks when every total is whole: a
    width of 1 gives a bar per mark."""
    if not totals or not max_marks:
        return []
    whole = all(total.is_integer() for total in totals)
    suggested = max_marks / (2 * math.cbrt(len(totals)))
    widths = [width for width in BAND_WIDTHS if max_marks / width <= MAX_BARS and not (whole and width % 1)]
    nearest = min(range(len(widths)), key=lambda i: abs(math.log(widths[i] / suggested)))
    width = widths[min(max(nearest - finer, 0), len(widths) - 1)]
    if whole and width == 1:
        counts = Counter(int(total) for total in totals)
        return [Band(mark, mark, counts[mark]) for mark in range(int(max_marks) + 1)]
    bands = math.ceil(max_marks / width)
    counts = Counter(min(int(total // width), bands - 1) for total in totals)
    return [Band(i * width, min((i + 1) * width, max_marks), counts[i]) for i in range(bands)]


def results(assessment: Assessment, students: Sequence[Student] | None = None) -> Results:
    """The marks of `students`, or else of every student."""
    questions = list(assessment.questions.all())
    students = list(assessment.students.all()) if students is None else students
    awarded = awards(questions, students)
    rows = [
        Row(student, {question.key: awarded[student.pk, question.pk] for question in questions}) for student in students
    ]
    return Results(assessment, questions, rows)


def average_proportions(assessment: Assessment) -> dict[UUID, float]:
    """The average proportion of each question's marks, over the students with marks for it, by question ID: the
    summaries' alone, without reading the answers."""
    questions = list(assessment.questions.all())
    students = list(assessment.students.only("file_marks"))
    awarded = awards(questions, students)
    averages = {}
    for question in questions:
        marks = [given for student in students if (given := awarded[student.pk, question.pk].marks) is not None]
        if marks and question.core.max_marks:
            averages[question.pk] = statistics.fmean(marks) / question.core.max_marks
    return averages
