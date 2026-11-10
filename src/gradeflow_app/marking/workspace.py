"""A user's draft of a marking, as the core edits it, with the forms of its steps and the answers they suggest; and
starting, saving and discarding it."""

from collections import Counter
from dataclasses import dataclass
from functools import cache, cached_property
from typing import Any, Self
from uuid import UUID

from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from gradeflow_core import Methods, ModelEndpoint, QuestionSet
from gradeflow_core.editing import MarkingDraft, Position, dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.courses.models import Course
from gradeflow_app.editor.editing import Conflict, EditorActions, StepWorkspace
from gradeflow_app.imports.models import answer_expression, has_imported_marks
from gradeflow_app.jobs import runs
from gradeflow_app.libraries.collection import OfferedMethods, offers, usable
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.marking.checks import marking_draft_of
from gradeflow_app.marking.models import Marking, MarkingDraftRecord, MarkingSubject
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.requests import Request

MAX_SUGGESTIONS = 100
"""The most common answers offered as suggestions."""
PREVIEW = "preview"
"""The kind of run that previews a draft."""


@dataclass
class MarkingWorkspace(StepWorkspace):
    """Build one per request: it reads every student's answer."""

    draft: MarkingDraftRecord

    @classmethod
    def of(
        cls, request: Request, access: AssessmentAccess, draft_id: UUID, *, lock: bool = False, **filters: Any
    ) -> Self:
        """The user's draft `draft_id` of the assessment; with `lock`, locked with the assessment for a change."""
        drafts = MarkingDraftRecord.objects.select_related("assessment", "question", "marking")
        if lock:
            access.assessment.lock()
            drafts = drafts.select_for_update(of=("self",))
        return cls(get_object_or_404(drafts, pk=draft_id, user=request.user, assessment=access.assessment, **filters))

    @cached_property
    def subject(self) -> MarkingSubject:
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
    def marking_draft(self) -> MarkingDraft:
        return marking_draft_of(self.draft.definition, self.questions, self.subject.key, self.methods)

    def save(self) -> None:
        """Saves the draft; once it is finished, as the core writes it, without what it leaves at defaults."""
        if not self.marking_draft.data:
            return
        self.draft.definition = dump_yaml(
            self.marking_draft.data if self.marking_draft.problems else self.marking_draft.finalized().dump()
        )
        self.draft.save(update_fields=["definition"])

    def save_marking(self, model_endpoint: ModelEndpoint | None) -> Marking:
        """Save the draft as the subject's marking, end the draft, and mark the students with it, asking language
        models at `model_endpoint` if it does."""
        subject = self.subject
        marking = subject.marking or Marking(assessment=subject.assessment, question=subject.question)
        marking.definition = self.draft.definition
        marking.save()
        runs.stop(PREVIEW, self.draft.pk)
        self.draft.delete()
        schedule_marking(marking, self.draft.user, model_endpoint)
        notify_change(topic("questions", subject.assessment.pk))
        return marking

    def discard(self) -> None:
        """End the draft, leaving the saved marking as it was."""
        runs.stop(PREVIEW, self.draft.pk)
        self.draft.delete()
        notify_drafts_changed(self.draft.assessment, self.draft.user)

    @cached_property
    def suggestions(self) -> dict[str, int]:
        """The question's commonest answers, with how many gave each."""
        question = self.subject.question
        if question is None:
            return {}
        answers = self.subject.assessment.students.values_list(answer_expression(question.key), flat=True)
        normalize = cache(question.core_question.normalize)
        counts = Counter(text for answer in answers if normalize(answer) and (text := answer.strip()))
        return dict(counts.most_common(MAX_SUGGESTIONS))

    def suggestions_at(self, position: Position) -> dict[str, int]:
        """What a step at `position` reads of the commonest answers, with how many gave each."""
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


@transaction.atomic
def start_draft(subject: MarkingSubject, user: User, definition: str) -> None:
    """Start the user's draft of the subject's marking from `definition`, replacing any they had."""
    subject.assessment.lock()
    draft_target = {"question": subject.question} if subject.question else {"marking": subject.marking}
    MarkingDraftRecord.objects.update_or_create(
        user=user,
        **draft_target,
        defaults={
            "assessment": subject.assessment,
            "definition": definition,
            "saved_marking_updated_at": subject.marking.updated_at if subject.marking else None,
            "preview_input": None,
            "preview_results": None,
            "preview_failure": "",
        },
    )
    notify_drafts_changed(subject.assessment, user)


def notify_drafts_changed(assessment: Assessment, user: User) -> None:
    notify_change(topic("drafts", assessment.pk, user.pk))


def _name_and_url(marking: Marking) -> tuple[str, str]:
    """A marking's name, its question or the questions it marks, and its page."""
    if marking.question is not None:
        return marking.question.key, reverse("question", args=[marking.assessment_id, marking.question_id])
    return ", ".join(sorted(marking.questions_given_marks)), reverse(
        "question_group", args=[marking.assessment_id, marking.pk]
    )
