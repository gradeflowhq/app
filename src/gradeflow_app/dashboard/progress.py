"""How far an assessment is on its way to marks in Canvas: imported, marked, then sent."""

from collections.abc import Mapping, Set
from dataclasses import dataclass
from enum import StrEnum

from django.db.models import QuerySet
from gradeflow_core import Methods, Step

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.exports.models import SentMarks
from gradeflow_app.imports.models import SubmissionsFile, imported_mark_keys_of
from gradeflow_app.libraries.collection import course_methods
from gradeflow_app.marking.checks import accepted_checks
from gradeflow_app.marking.models import Marking


class Stage(StrEnum):
    IMPORT = "import"
    MARK = "mark"
    SEND = "send"
    SENT = "sent"


@dataclass(frozen=True)
class Progress:
    latest_import: SubmissionsFile | None
    question_count: int
    marked_keys: frozenset[str]
    """The questions with marks: from an accepted marking, or imported with the submissions file."""
    latest_sent_marks: SentMarks | None

    @property
    def marked_count(self) -> int:
        return len(self.marked_keys)

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


def progress(assessment: Assessment, accepted: Mapping[Marking, Step], imported_keys: Set[str]) -> Progress:
    """The assessment's progress, with `accepted`, its markings the core accepts, and `imported_keys`, the questions
    with imported marks."""
    return Progress(
        latest_import=assessment.submissions_files.first(),
        question_count=assessment.questions.count(),
        marked_keys=frozenset(key for check in accepted.values() for key in check.questions_marked()) | imported_keys,
        latest_sent_marks=assessment.sent_marks.first(),
    )


def progress_of(assessments: QuerySet[Assessment]) -> list[tuple[Assessment, Progress]]:
    """Each of the assessments, all of one course, with its progress, read in as many queries for any number."""
    assessment_list = list(assessments.prefetch_related("questions", "markings", "submissions_files", "sent_marks"))
    methods = course_methods(assessment_list[0].course) if assessment_list else Methods()
    imported_keys = imported_mark_keys_of(assessment_list)
    return [
        (
            assessment,
            progress(
                assessment,
                accepted_checks(assessment.markings.all(), methods, assessment.question_set()),
                imported_keys.get(assessment.pk, set()),
            ),
        )
        for assessment in assessment_list
    ]
