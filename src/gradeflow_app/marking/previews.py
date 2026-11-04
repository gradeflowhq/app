"""A draft's preview: every student marked with it before it is saved."""

import json

from django.db import transaction
from gradeflow_core import MarkedSubmission, QuestionResult
from gradeflow_core.editing import methods_used

from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.libraries.collection import files_needed_by
from gradeflow_app.live.topics import topic
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import MarkingDraftRecord
from gradeflow_app.marking.remarks import mark_students
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.requests import Request

type PreviewResults = dict[str, dict[str, QuestionResult]]
"""Student → question key → result."""


def latest_preview(draft: MarkingDraftRecord) -> Job | None:
    if draft.preview_id is None:
        return None
    if timed_out := Job.objects.timed_out().filter(pk=draft.preview_id).first():
        queue.end(timed_out, queue.TIMED_OUT_REASON)
    job = Job.objects.filter(pk=draft.preview_id).first()
    return job if job is not None and job.ran_on_this_core else None


def preview_results(job: Job) -> PreviewResults:
    submissions = [MarkedSubmission.model_validate(item) for item in job.result or []]
    return {submission.student_id: submission.results for submission in submissions}


def preview_and_results(workspace: MarkingWorkspace | None) -> tuple[Job | None, PreviewResults | None]:
    """The draft's latest preview, and how it marked each student once it has finished."""
    job = latest_preview(workspace.draft) if workspace else None
    return job, preview_results(job) if job and job.status == Job.Status.COMPLETED else None


@transaction.atomic
def run_preview(request: Request, membership: AssessmentAccess, workspace: MarkingWorkspace) -> None:
    membership.assessment.lock()
    draft = (
        MarkingDraftRecord.objects.select_for_update(of=("self",)).select_related("preview").get(pk=workspace.draft.pk)
    )
    current = MarkingWorkspace(draft)
    if not current.valid:
        return
    check = current.checks.finalized()
    preview_input = _preview_input(current)
    last = latest_preview(draft)
    if last and last.status != Job.Status.FAILED and draft.preview_input == preview_input:
        return
    if draft.preview is not None:
        queue.cancel_jobs([draft.preview])
    model_endpoint = endpoint(request) if check.needs_model_endpoint() else None
    methods = files_needed_by(methods_used(check.dump()))
    draft.preview = mark_students(
        membership.assessment, check, methods, model_endpoint, kind=Job.Kind.PREVIEW, user=request.user
    )
    draft.preview_input = preview_input
    draft.save(update_fields=["preview", "preview_input"])
    workspace.draft.refresh_from_db()


def preview_is_stale(workspace: MarkingWorkspace) -> bool:
    """Whether the draft marks differently from what its preview, or else the saved marking, shows."""
    draft = workspace.draft
    if draft.preview_id is not None:
        return draft.preview_input != _preview_input(workspace)
    saved = draft.subject.marking
    return saved is None or definition_data(saved.definition) != definition_data(draft.definition)


def _preview_input(workspace: MarkingWorkspace) -> str | None:
    """What a preview of the draft marks with: its definition and the questions it marks or reads, as they are now;
    None while it can't mark."""
    if (check := workspace.finalized) is None:
        return None
    keys = sorted(check.questions_marked() | check.questions_read())
    questions = {key: workspace.questions[key].model_dump(mode="json") for key in keys}
    return json.dumps({"definition": workspace.draft.definition, "questions": questions})


register(
    Job.Kind.PREVIEW,
    Handler(task="mark", topics=lambda job: [topic("preview", job.assessment_id, job.created_by_id)]),
)
