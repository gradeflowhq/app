"""How far an assessment is on its way to marks in Canvas: imported, marked, then sent."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from django.db.models import QuerySet
from gradeflow_core import Check, Methods

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.imports.models import ResultsFile
from gradeflow_app.libraries.collection import in_use
from gradeflow_app.marking.checks import accepted, marked_keys
from gradeflow_app.marking.models import Marking
from gradeflow_app.publish.models import Publication


class Stage(StrEnum):
    IMPORT = "import"
    MARK = "mark"
    SEND = "send"
    SENT = "sent"


@dataclass(frozen=True)
class Progress:
    imported: ResultsFile | None
    questions: int
    marked: int
    sent: Publication | None

    @property
    def marking_done(self) -> bool:
        return self.marked >= self.questions

    @property
    def stage(self) -> Stage:
        """What the assessment needs next."""
        if self.imported is None:
            return Stage.IMPORT
        if not self.marking_done:
            return Stage.MARK
        return Stage.SEND if self.sent is None else Stage.SENT


def progress(assessment: Assessment, accepted: Mapping[Marking, Check]) -> Progress:
    """The assessment's progress, with `accepted`, its markings the core accepts."""
    return Progress(
        imported=assessment.results_files.first(),
        questions=assessment.questions.count(),
        marked=len(marked_keys(accepted)),
        sent=assessment.publications.first(),
    )


def progress_of(assessments: QuerySet[Assessment]) -> list[tuple[Assessment, Progress]]:
    """Each of the assessments, all of one course, with its progress, read in as many queries for any number."""
    rows = list(assessments.prefetch_related("questions", "markings", "results_files", "publications"))
    methods = in_use(rows[0].course) if rows else Methods()
    return [
        (assessment, progress(assessment, accepted(assessment.markings.all(), methods, assessment.question_set())))
        for assessment in rows
    ]
