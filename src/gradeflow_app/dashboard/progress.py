"""How far an assessment is on its way to marks in Canvas: imported, marked, then sent."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from django.db.models import QuerySet
from gradeflow_core import Check, Methods

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.imports.models import ResultsFile
from gradeflow_app.libraries.collection import course_methods
from gradeflow_app.marking.checks import accepted_checks, keys_marked_by
from gradeflow_app.marking.models import Marking
from gradeflow_app.publish.models import SentMarks


class Stage(StrEnum):
    IMPORT = "import"
    MARK = "mark"
    SEND = "send"
    SENT = "sent"


@dataclass(frozen=True)
class Progress:
    latest_import: ResultsFile | None
    question_count: int
    marked_count: int
    latest_sent_marks: SentMarks | None

    @property
    def all_marked(self) -> bool:
        return self.marked_count >= self.question_count

    @property
    def stage(self) -> Stage:
        """What the assessment needs next."""
        if self.latest_import is None:
            return Stage.IMPORT
        if not self.all_marked:
            return Stage.MARK
        return Stage.SEND if self.latest_sent_marks is None else Stage.SENT


def progress(assessment: Assessment, accepted: Mapping[Marking, Check]) -> Progress:
    """The assessment's progress, with `accepted`, its markings the core accepts."""
    return Progress(
        latest_import=assessment.results_files.first(),
        question_count=assessment.questions.count(),
        marked_count=len(keys_marked_by(accepted)),
        latest_sent_marks=assessment.sent_marks.first(),
    )


def progress_of(assessments: QuerySet[Assessment]) -> list[tuple[Assessment, Progress]]:
    """Each of the assessments, all of one course, with its progress, read in as many queries for any number."""
    assessment_list = list(assessments.prefetch_related("questions", "markings", "results_files", "sent_marks"))
    methods = course_methods(assessment_list[0].course) if assessment_list else Methods()
    return [
        (
            assessment,
            progress(assessment, accepted_checks(assessment.markings.all(), methods, assessment.question_set())),
        )
        for assessment in assessment_list
    ]
