"""A user's draft of a marking, as the core edits it, with the forms of its checks and the answers they suggest."""

from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from typing import Any, Self
from uuid import UUID

from django.shortcuts import get_object_or_404
from django.urls import reverse
from gradeflow_core import Methods, QuestionSet
from gradeflow_core.editing import MarkingDraft, Position, dump_yaml

from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.courses.models import Course
from gradeflow_app.editor.editing import CheckWorkspace, Conflict, EditorActions
from gradeflow_app.imports.models import answer_expression, has_imported_marks
from gradeflow_app.libraries.collection import OfferedMethods, offers, usable
from gradeflow_app.marking.checks import marking_draft
from gradeflow_app.marking.models import Marking, MarkingDraftRecord, MarkingTarget
from gradeflow_app.requests import Request

MAX_SUGGESTIONS = 100
"""The most common answers offered as suggestions."""


@dataclass
class MarkingWorkspace(CheckWorkspace):
    """Build one per request: it reads every student's answer."""

    draft: MarkingDraftRecord

    @classmethod
    def of(
        cls, request: Request, membership: AssessmentAccess, draft_id: UUID, *, lock: bool = False, **filters: Any
    ) -> Self:
        """The user's draft `draft_id` of the assessment; with `lock`, locked with the assessment for a change."""
        drafts = MarkingDraftRecord.objects.select_related("assessment", "question", "marking")
        if lock:
            membership.assessment.lock()
            drafts = drafts.select_for_update(of=("self",))
        return cls(
            get_object_or_404(drafts, pk=draft_id, user=request.user, assessment=membership.assessment, **filters)
        )

    @property
    def subject(self) -> MarkingTarget:
        return self.draft.subject

    @property
    def course(self) -> Course:
        return self.subject.assessment.course

    @cached_property
    def offers(self) -> OfferedMethods:
        return offers(self.draft.user, self.course)

    @property
    def actions(self) -> EditorActions:
        args = [self.draft.assessment_id, self.draft.pk]
        return EditorActions(
            update=reverse("draft", args=args),
            methods=reverse("draft_methods", args=args),
            yaml=reverse("draft_yaml", args=args),
            discard=reverse("draft_discard", args=args),
            save=reverse("draft_save", args=args),
        )

    @property
    def discard_note(self) -> str:
        subject = self.subject
        if subject.marking is not None:
            return "The saved marking stays as it was."
        if subject.key and has_imported_marks(subject.assessment, subject.key):
            return "Students keep their imported marks."
        return ""

    @property
    def saved_by_someone_else(self) -> bool:
        saved = self.subject.marking
        return (
            saved is not None
            and self.draft.saved_marking_updated_at is not None
            and saved.updated_at > self.draft.saved_marking_updated_at
        )

    @cached_property
    def questions(self) -> QuestionSet:
        return self.subject.assessment.question_set()

    @cached_property
    def methods(self) -> Methods:
        return usable(self.draft.user, self.course)

    @cached_property
    def checks(self) -> MarkingDraft:
        return marking_draft(self.draft.definition, self.questions, self.subject.key, self.methods)

    def save(self) -> None:
        """Saves the draft; once it is finished, as the core writes it, without what it leaves at defaults."""
        if not self.checks.data:
            return
        self.draft.definition = dump_yaml(self.checks.data if self.checks.problems else self.checks.finalized().dump())
        self.draft.save(update_fields=["definition"])

    @cached_property
    def suggestions(self) -> dict[str, int]:
        """The question's commonest answers, with how many gave each."""
        if self.subject.key is None:
            return {}
        answers = self.subject.assessment.students.values_list(answer_expression(self.subject.key), flat=True)
        counts = Counter(text for answer in answers if (text := answer.strip()))
        return dict(counts.most_common(MAX_SUGGESTIONS))

    def suggestions_at(self, position: Position) -> dict[str, int]:
        """What a check at `position` reads of the commonest answers, with how many gave each."""
        if position.question != self.subject.key:
            return {}
        if position.value is None:
            return self.suggestions
        counts: Counter[str] = Counter()
        for answer, count in self.suggestions.items():
            if value := position.answer_value(answer):
                counts[value] += count
        return dict(counts.most_common())

    @cached_property
    def conflicts(self) -> list[Conflict]:
        """The assessment's other markings of the questions this one marks, once it is finished. A question has one
        marking."""
        if (marking := self.finalized) is None:
            return []
        marked_keys = marking.questions_marked()
        return [
            Conflict(sorted(marked_keys & other.questions_given_marks), *_name_and_url(other))
            for other in self.other_markings
            if marked_keys & other.questions_given_marks
        ]

    @cached_property
    def other_markings(self) -> list[Marking]:
        saved = self.subject.marking
        markings = self.subject.assessment.markings.select_related("question")
        return list(markings.exclude(pk=saved.pk) if saved is not None else markings)


def _name_and_url(marking: Marking) -> tuple[str, str]:
    """A marking's name, its question or the questions it marks, and its page."""
    if marking.question is not None:
        return marking.question.key, reverse("question", args=[marking.assessment_id, marking.question_id])
    return ", ".join(sorted(marking.questions_given_marks)), reverse(
        "question_group", args=[marking.assessment_id, marking.pk]
    )
