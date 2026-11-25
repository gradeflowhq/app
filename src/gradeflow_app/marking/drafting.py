"""Drafting a question's marking with AI from the lecturer's marking instructions: a run of the core's
`draft_marking`, whose marking replaces the user's draft."""

import secrets
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from gradeflow_core import GradeFlowError, MarkScheme
from gradeflow_core.editing import dump_yaml, methods_used
from pydantic import BaseModel

from gradeflow_app.accounts.models import User
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.payloads import DraftRun
from gradeflow_app.jobs.runner import Ending, Run
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.libraries.collection import course_method_files, files_needed_by
from gradeflow_app.live.topics import topic
from gradeflow_app.marking.models import MarkingDraftRecord, MarkingSubject
from gradeflow_app.marking.previews import start_preview
from gradeflow_app.marking.remarks import student_submissions
from gradeflow_app.marking.workspace import DRAFTING, MarkingWorkspace, notify_drafts_changed, start_draft
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.requests import Request

NO_INSTRUCTIONS = "Write your marking instructions."
NOT_CONNECTED = "Connect your AI service to draft with it."
NO_DRAFT_MODEL = "Choose a draft model for your AI service."


@dataclass(frozen=True)
class Drafting:
    """Drafting a question's marking with AI from the user's marking instructions, as their marking panel shows it."""

    question_id: str
    instructions: str
    is_update: bool
    """Whether AI updates an existing marking rather than writing a new one."""
    state: RunState | None
    """How drafting is going, or why it failed."""
    summary: str = ""
    """What AI said it did, until it's dismissed or the draft changes."""
    start_error: str = ""
    """Why drafting couldn't start."""

    @property
    def is_running(self) -> bool:
        return self.state is not None and self.state.is_active

    @property
    def is_editing(self) -> bool:
        """Whether the band opens on its field: after drafting failed or couldn't start, or on a new marking's
        instructions."""
        return not self.is_running and bool(
            self.start_error or self.state or (self.instructions and not self.is_update)
        )

    @property
    def is_shown(self) -> bool:
        """Whether the band shows on load: with its field open, while drafting runs, or with a marking's
        instructions."""
        return self.is_editing or self.is_running or bool(self.is_update and self.instructions)

    @property
    def needs_ai_service(self) -> bool:
        """Whether drafting couldn't start for want of the AI service or its draft model."""
        return self.start_error in (NOT_CONNECTED, NO_DRAFT_MODEL)


def drafting_of(subject: MarkingSubject, user: User) -> Drafting | None:
    """The user's drafting of the question's marking; None for a question group's."""
    if subject.question is None:
        return None
    draft = subject.user_draft(user)
    current = draft or subject.marking
    return Drafting(
        question_id=str(subject.question.pk),
        instructions=current.instructions if current else "",
        is_update=bool(draft.definition) if draft else subject.marking is not None,
        state=_state(draft) if draft else None,
        summary=draft.drafting_summary if draft else "",
    )


def _state(draft: MarkingDraftRecord) -> RunState | None:
    """How the draft's drafting is going, or why it failed; None when it isn't drafting and didn't fail. A key the
    runner doesn't hold is a lost run: the run starts once the key is saved."""
    if draft.drafting_run_key:
        return runs.state(DRAFTING, draft.pk, draft.drafting_run_key) or RunState(
            "failed", runs.ENDING_REASONS[Ending.LOST]
        )
    return RunState("failed", draft.drafting_failure) if draft.drafting_failure else None


@transaction.atomic
def start_drafting(request: Request, subject: MarkingSubject, instructions: str) -> str:
    """Draft the subject's marking from `instructions` with AI, starting a draft if the user has none. Returns why it
    couldn't start, or `""`."""
    if not instructions:
        return NO_INSTRUCTIONS
    model_endpoint = endpoint(request)
    if model_endpoint is None:
        return NOT_CONNECTED
    if model_endpoint.draft_model is None:
        return NO_DRAFT_MODEL
    subject.assessment.lock()
    draft = subject.user_draft(request.user, lock=True) or start_draft(
        subject, request.user, subject.marking.definition if subject.marking else ""
    )
    draft.instructions, draft.drafting_run_key = instructions, secrets.token_hex(8)
    draft.drafting_failure, draft.drafting_summary = "", ""
    draft.save(update_fields=["instructions", "drafting_run_key", "drafting_failure", "drafting_summary"])
    runs.start(Run(DRAFTING, str(draft.pk), draft.drafting_run_key), model_endpoint)
    return ""


@transaction.atomic
def cancel_drafting(subject: MarkingSubject, user: User) -> None:
    """Stop drafting, leaving the user's draft as it was."""
    if (draft := subject.user_draft(user, lock=True)) is None:
        return
    runs.stop(DRAFTING, draft.pk)
    draft.drafting_run_key = ""
    draft.save(update_fields=["drafting_run_key"])


def dismiss_drafting_summary(subject: MarkingSubject, user: User) -> None:
    """Stop showing what AI said it did."""
    MarkingDraftRecord.objects.filter(user=user, question=subject.question).update(drafting_summary="")


@transaction.atomic
def remove_instructions(subject: MarkingSubject, user: User) -> None:
    """Remove the marking instructions from the user's draft, leaving its marking as it is; a draft with no marking
    yet is discarded."""
    if (draft := subject.user_draft(user, lock=True)) is None or draft.drafting_run_key:
        return
    if not draft.definition:
        MarkingWorkspace(draft).discard()
        return
    draft.instructions, draft.drafting_summary, draft.drafting_failure = "", "", ""
    draft.save(update_fields=["instructions", "drafting_summary", "drafting_failure"])


def _draft(run: Run) -> MarkingDraftRecord | None:
    """The run's draft, while the run is still the one drafting it."""
    return (
        MarkingDraftRecord.objects.select_related("assessment", "question")
        .filter(pk=run.owner, drafting_run_key=run.key)
        .first()
    )


def _input(run: Run) -> DraftRun | None:
    if (draft := _draft(run)) is None or draft.question is None:
        return None
    workspace = MarkingWorkspace(draft)
    marking = workspace.finalized
    current = marking.dump() if marking is not None else None
    keys_read = {draft.question.key} | (marking.questions_read() if marking is not None else set())
    methods = set(course_method_files(draft.assessment.course)) | set(files_needed_by(methods_used(current)))
    return DraftRun(
        questions=workspace.questions,
        question=draft.question.key,
        instructions=draft.instructions,
        marking=current,
        methods=sorted(methods),
        submissions=student_submissions(draft.assessment, keys_read),
    )


class _DraftOutcome(BaseModel):
    marking: dict[str, Any]
    summary: str


def _store_marking(run: Run, outcome: Any) -> None:
    drafted = _DraftOutcome.model_validate(outcome)
    if (draft := _draft(run)) is None:
        raise ValueError("The draft is no longer drafting with this run.")
    workspace = MarkingWorkspace(draft)
    try:
        MarkScheme.load([drafted.marking], methods=workspace.methods)
    except GradeFlowError as error:
        raise ValueError(str(error)) from None
    draft.definition, draft.drafting_summary, draft.drafting_run_key = dump_yaml(drafted.marking), drafted.summary, ""
    draft.save(update_fields=["definition", "drafting_summary", "drafting_run_key"])
    if (marking := workspace.finalized) is not None and not marking.needs_model_endpoint():
        start_preview(workspace)
    notify_drafts_changed(draft.assessment, draft.user)


def _record_failure(run: Run, error: str) -> None:
    MarkingDraftRecord.objects.filter(pk=run.owner, drafting_run_key=run.key).update(
        drafting_run_key="", drafting_failure=error
    )


def _lock(run: Run) -> None:
    MarkingDraftRecord.objects.select_for_update().filter(pk=run.owner).first()


def _topics(run: Run) -> list[str]:
    draft = MarkingDraftRecord.objects.filter(pk=run.owner).first()
    return [topic(DRAFTING, draft.question_id, draft.user_id)] if draft is not None else []


register(
    DRAFTING,
    Handler(
        task="draft",
        topics=_topics,
        input=_input,
        is_current=lambda run: _draft(run) is not None,
        finish=_store_marking,
        fail=_record_failure,
        lock=_lock,
    ),
)
