"""A draft's preview: every student marked with it before it is saved."""

import json

from django.db import transaction
from gradeflow_core import MarkedSubmission, QuestionResult
from gradeflow_core.editing import methods_used

from gradeflow_app.assessments.models import Membership
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.libraries.collection import needed
from gradeflow_app.live.topics import topic
from gradeflow_app.marking.checks import tree_of
from gradeflow_app.marking.models import Draft
from gradeflow_app.marking.remarks import mark_students
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.model_service.endpoints import endpoint, for_checks
from gradeflow_app.requests import Request

type Previewed = dict[str, dict[str, QuestionResult]]
"""Student → question key → result."""


def latest(draft: Draft) -> Job | None:
    if draft.preview_id is None:
        return None
    if lost := Job.objects.lost().filter(pk=draft.preview_id).first():
        queue.finish(lost, queue.LOST)
    found = Job.objects.filter(pk=draft.preview_id).first()
    return found if found is not None and found.by_this_core else None


def results(job: Job) -> Previewed:
    marked = [MarkedSubmission.model_validate(item) for item in job.result or []]
    return {submission.student_id: submission.results for submission in marked}


def previewed(workspace: Workspace | None) -> tuple[Job | None, Previewed | None]:
    """The draft's latest preview, and how it marked each student once it has finished."""
    job = latest(workspace.draft) if workspace else None
    return job, results(job) if job and job.status == Job.Status.COMPLETED else None


@transaction.atomic
def run(request: Request, membership: Membership, workspace: Workspace) -> None:
    membership.assessment.lock()
    draft = Draft.objects.select_for_update(of=("self",)).select_related("preview").get(pk=workspace.draft.pk)
    current = Workspace(draft)
    if not current.valid:
        return
    check = current.checks.finalized()
    marked = _marked_with(current)
    last = latest(draft)
    if last and last.status != Job.Status.FAILED and draft.previewed == marked:
        return
    if draft.preview is not None:
        queue.replace([draft.preview])
    model_endpoint = for_checks([check], endpoint(request))
    methods = needed(methods_used(check.dump()))
    draft.preview = mark_students(
        membership.assessment, check, methods, model_endpoint, kind=Job.Kind.PREVIEW, user=request.user
    )
    draft.previewed = marked
    draft.save(update_fields=["preview", "previewed"])
    workspace.draft.refresh_from_db()


def stale(workspace: Workspace) -> bool:
    """Whether the draft marks differently from what its preview, or else the saved marking, shows."""
    draft = workspace.draft
    if draft.preview_id is not None:
        return draft.previewed != _marked_with(workspace)
    saved = draft.subject.marking
    return saved is None or tree_of(saved.definition) != tree_of(draft.definition)


def _marked_with(workspace: Workspace) -> str | None:
    """What a preview of the draft marks with: its definition and the questions it marks or reads, as they are now;
    None while it can't mark."""
    if (check := workspace.finished) is None:
        return None
    keys = sorted(check.questions_marked() | check.questions_read())
    questions = {key: workspace.questions[key].model_dump(mode="json") for key in keys}
    return json.dumps({"definition": workspace.draft.definition, "questions": questions})


register(
    Job.Kind.PREVIEW,
    Handler[list[MarkedSubmission]](
        task="mark", topics=lambda job: [topic("preview", job.assessment_id, job.created_by_id)]
    ),
)
