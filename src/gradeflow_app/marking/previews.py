"""A draft's preview: every student marked with it before it is saved."""

import hashlib
import json
from typing import Any

from django.db import transaction
from gradeflow_core import MarkedSubmission, QuestionResult
from gradeflow_core.editing import methods_used
from pydantic import ValidationError

from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.jobs.runner import Run
from gradeflow_app.jobs.runs import COMPLETED, RunState
from gradeflow_app.libraries.collection import files_needed_by
from gradeflow_app.live.topics import topic
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import MarkingDraftRecord
from gradeflow_app.marking.remarks import marked_submissions, marking_run
from gradeflow_app.marking.workspace import PREVIEW, MarkingWorkspace
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.requests import Request

type PreviewResults = dict[str, dict[str, QuestionResult]]
"""Student → question key → result."""


def preview_state(draft: MarkingDraftRecord) -> RunState | None:
    """How the draft's latest preview is going, or went; None if there's none to show."""
    if draft.preview_input is None:
        return None
    key = _key(draft.preview_input)
    if (state := next((state for run, state in runs.active(PREVIEW, draft.pk) if run.key == key), None)) is not None:
        return state
    if draft.preview_failure:
        return RunState("failed", draft.preview_failure)
    return COMPLETED if draft.preview_results is not None else None


def preview_and_results(workspace: MarkingWorkspace | None) -> tuple[RunState | None, PreviewResults | None]:
    """The draft's latest preview, and how it marked each student once it has finished."""
    if workspace is None or (state := preview_state(workspace.draft)) is None:
        return None, None
    if state is not COMPLETED:
        return state, None
    try:
        submissions = [MarkedSubmission.model_validate(item) for item in workspace.draft.preview_results or []]
    except ValidationError:
        return None, None  # from a build of the core that marks differently
    return state, {submission.student_id: submission.results for submission in submissions}


@transaction.atomic
def run_preview(request: Request, access: AssessmentAccess, workspace: MarkingWorkspace) -> None:
    access.assessment.lock()
    draft = MarkingDraftRecord.objects.select_for_update().get(pk=workspace.draft.pk)
    current = MarkingWorkspace(draft)
    if not current.valid or (preview_input := _preview_input(current)) is None:
        return
    last = preview_state(draft)
    if last is not None and last.status != "failed" and draft.preview_input == preview_input:
        return
    draft.preview_input, draft.preview_results, draft.preview_failure = preview_input, None, ""
    draft.save(update_fields=["preview_input", "preview_results", "preview_failure"])
    asks_models = current.checks.finalized().needs_model_endpoint()
    runs.start(Run(PREVIEW, str(draft.pk), _key(preview_input)), endpoint(request) if asks_models else None)
    workspace.draft.refresh_from_db()


def preview_is_stale(workspace: MarkingWorkspace) -> bool:
    """Whether the draft marks differently from what its preview, or else the saved marking, shows."""
    draft = workspace.draft
    if draft.preview_input is not None:
        return draft.preview_input != _preview_input(workspace)
    saved = draft.subject.marking
    return saved is None or definition_data(saved.definition) != definition_data(draft.definition)


def _preview_input(workspace: MarkingWorkspace) -> str | None:
    """What a preview of the draft marks with: the core, the definition and the questions it marks or reads, as they
    are now; None while it can't mark."""
    if (check := workspace.finalized) is None:
        return None
    keys = sorted(check.questions_read())
    questions = {key: workspace.questions[key].model_dump(mode="json") for key in keys}
    return json.dumps({"core": CORE_VERSION, "definition": workspace.draft.definition, "questions": questions})


def _key(preview_input: str) -> str:
    return hashlib.sha256(preview_input.encode()).hexdigest()[:16]


def _draft(run: Run) -> MarkingDraftRecord | None:
    return MarkingDraftRecord.objects.select_related("assessment").filter(pk=run.owner).first()


def _workspace(run: Run) -> MarkingWorkspace | None:
    """The draft as it is, if it is still what the run previews."""
    draft = _draft(run)
    if draft is None or draft.preview_input is None or _key(draft.preview_input) != run.key:
        return None
    workspace = MarkingWorkspace(draft)
    return workspace if workspace.valid and _preview_input(workspace) == draft.preview_input else None


def _input(run: Run) -> MarkingRun | None:
    if (workspace := _workspace(run)) is None:
        return None
    check = workspace.checks.finalized()
    return marking_run(workspace.draft.assessment, check, files_needed_by(methods_used(check.dump())))


def _is_current(run: Run) -> bool:
    draft = _draft(run)
    return draft is not None and draft.preview_input is not None and _key(draft.preview_input) == run.key


def _store_results(run: Run, outcome: Any) -> None:
    workspace = _workspace(run)
    if workspace is None:
        raise ValueError("The draft changed since its preview started.")
    submissions = marked_submissions(
        outcome, workspace.draft.assessment, workspace.checks.finalized().questions_marked()
    )
    workspace.draft.preview_results = [submission.model_dump(mode="json") for submission in submissions]
    workspace.draft.save(update_fields=["preview_results"])


def _lock(run: Run) -> None:
    MarkingDraftRecord.objects.select_for_update().filter(pk=run.owner).first()


def _record_failure(run: Run, error: str) -> None:
    MarkingDraftRecord.objects.filter(pk=run.owner).update(preview_failure=error)


def _topics(run: Run) -> list[str]:
    draft = _draft(run)
    return [topic("preview", draft.assessment_id, draft.user_id)] if draft is not None else []


register(
    PREVIEW,
    Handler(
        task="mark",
        topics=_topics,
        input=_input,
        is_current=_is_current,
        finish=_store_results,
        fail=_record_failure,
        lock=_lock,
    ),
)
