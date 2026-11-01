"""What sandbox jobs report, applied in the order of the edits they marked."""

from collections.abc import Sequence
from typing import Any, Self

from django.db import transaction
from django.db.models import F
from django.utils import timezone
from gradeflow_core import AnswerComparison, MarkedSubmission
from gradeflow_core.editing import ModelTrial, RowResult
from pydantic import BaseModel, TypeAdapter, ValidationError, model_validator

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs.handlers import Task, handler
from gradeflow_app.jobs.models import Batch, Job
from gradeflow_app.live.topics import publish

UNREADABLE = "The marking service sent results that can't be read."


def _lock(job: Job) -> Job:
    if job.assessment_id is not None:
        Assessment.objects.select_for_update().get(pk=job.assessment_id)
    handler(job).lock(job)
    return Job.objects.select_for_update().get(pk=job.pk)


@transaction.atomic
def started(job: Job) -> None:
    job = _lock(job)
    if job.status != Job.Status.QUEUED:
        return
    job.status, job.started_at = Job.Status.RUNNING, timezone.now()
    job.admitted_at = job.admitted_at or job.started_at
    job.save(update_fields=["status", "started_at", "admitted_at"])
    changed(job)


@transaction.atomic
def compared(job: Job, event: dict[str, Any]) -> None:
    job = _lock(job)
    if not job.active or not job.input:
        return
    sent = _Part.model_validate(event)
    result = job.result or {"parts": [], "total": sent.total}
    parts = result["parts"]
    if result["total"] != sent.total or sent.index > len(parts):
        raise ValueError("Comparison parts must arrive in order.")
    if sent.index < len(parts):
        if parts[sent.index] != sent.part:
            raise ValueError("A comparison part cannot change.")
        return
    parts.append(sent.part)
    job.result, job.progress = result, len(parts) / sent.total
    job.save(update_fields=["result", "progress"])
    changed(job)


class _Part(BaseModel, strict=True):
    """A piece of a comparison's JSON: one of `total`, from 0."""

    index: int
    total: int
    part: str

    @model_validator(mode="after")
    def _one_of_them(self) -> Self:
        if not 0 <= self.index < self.total:
            raise ValueError("Invalid comparison part.")
        return self


@transaction.atomic
def marked(job: Job, submissions: Sequence[dict[str, Any]]) -> None:
    job = _lock(job)
    if not job.active or not job.input or not handler(job).current(job):
        return
    try:
        sent = [MarkedSubmission.model_validate(item).model_dump(mode="json") for item in submissions]
    except ValidationError:
        fail(job, UNREADABLE)
        return
    if not job.batches.filter(submissions=sent).exists():
        Batch.objects.create(job=job, submissions=sent)
        total = len(job.input["submissions"])
        Job.objects.filter(pk=job.pk).update(progress=F("progress") + len(sent) / max(total, 1))
        changed(job)


def tested(job: Job, results: object) -> None:
    _keep(job, _ROWS, results, current=True)


def chosen(job: Job, choice: object) -> None:
    _keep(job, _CHOICE, choice)


def tried(job: Job, trials: object) -> None:
    _keep(job, TRIALS, trials)


@transaction.atomic
def _keep(job: Job, read: TypeAdapter[Any], sent: object, *, current: bool = False) -> None:
    """Keep what the job sent, as `read` reads it, for its result; with `current`, only while its outcome still
    applies."""
    job = _lock(job)
    if not job.active or (current and not handler(job).current(job)):
        return
    try:
        job.result = read.dump_python(read.validate_python(sent), mode="json")
    except ValidationError:
        fail(job, UNREADABLE)
        return
    job.save(update_fields=["result"])


@transaction.atomic
def progressed(job: Job, event: dict[str, Any]) -> None:
    job = _lock(job)
    if not job.active:
        return
    try:
        progress = _Progress.model_validate(event)
    except ValidationError:
        return
    job.step = f"{progress.step[:150]}: {progress.done} of {progress.total}"
    job.save(update_fields=["step"])
    changed(job)


class _Progress(BaseModel):
    done: int
    total: int
    step: str


class _Choice(BaseModel):
    decision_model: str | None
    reasoning_model: str | None
    trials: list[ModelTrial]


_ROWS = TypeAdapter(list[RowResult])
_CHOICE = TypeAdapter(_Choice)
TRIALS = TypeAdapter(list[ModelTrial])


@transaction.atomic
def complete(job: Job) -> None:
    job = _lock(job)
    if not job.active:
        return
    kind = handler(job)
    if not kind.current(job):
        fail(job, "Out of date.")
        return
    try:
        outcome, kept = _outcome(job, kind.task)
    except ValueError as error:
        fail(job, str(error))
        return
    if kind.finish is not None:
        kind.finish(job, outcome)
    job.result = None if kind.finish is not None else kept
    job.batches.all().delete()
    job.status, job.progress, job.finished_at, job.step = Job.Status.COMPLETED, 1, timezone.now(), ""
    job.input = None
    job.save()
    changed(job)


def _outcome(job: Job, task: Task) -> tuple[Any, Any]:
    """What the job's task gave, and the JSON the job keeps of it as its result. Raises `ValueError` if it doesn't
    answer what the job ran on."""
    match task:
        case "mark":
            sent = _submissions(job)
            return sent, [submission.model_dump(mode="json") for submission in sent]
        case "compare":
            return _comparison(job), None
        case "test":
            return _test_results(job), job.result
        case "choose" | "try":
            if job.result is None:
                raise ValueError("The marking service didn't say how the models did.")
            return job.result, job.result


def _submissions(job: Job) -> list[MarkedSubmission]:
    sent = [MarkedSubmission.model_validate(item) for batch in job.batches.all() for item in batch.submissions]
    expected = {item["student_id"] for item in (job.input or {}).get("submissions", [])}
    received = {submission.student_id for submission in sent}
    missing_outputs = any(set(job.questions) - submission.results.keys() for submission in sent)
    if received != expected or len(sent) != len(received) or missing_outputs:
        raise ValueError("The marking service returned an incomplete set of student results.")
    return sent


def _test_results(job: Job) -> list[dict[str, Any]]:
    rows = (job.input or {}).get("rows", [])
    results: list[dict[str, Any]] = job.result or []
    if len(results) != len(rows) or any(
        result["answers"] != row["answers"] for result, row in zip(results, rows, strict=True)
    ):
        raise ValueError("The marking service returned results that don't match the test answers.")
    return results


def _comparison(job: Job) -> AnswerComparison:
    if not job.result or len(job.result["parts"]) != job.result["total"]:
        raise ValueError("The marking service returned an incomplete answer comparison.")
    try:
        comparison = AnswerComparison.model_validate_json("".join(job.result["parts"]))
    except ValueError as error:
        raise ValueError("The marking service returned an invalid answer comparison.") from error
    if comparison.texts != tuple((job.input or {}).get("texts", [])) or comparison.similar != "meaning":
        raise ValueError("The answer comparison does not match this job's inputs.")
    return comparison


def fail(job: Job, error: str) -> None:
    fail_all([job], error)


@transaction.atomic
def fail_all(failed: Sequence[Job], error: str) -> None:
    names: set[str] = set()
    for candidate in sorted(
        failed, key=lambda item: (str(item.course_id), str(item.assessment_id), str(item.marking_id), str(item.pk))
    ):
        job = _lock(candidate)
        if not job.active:
            continue
        job.status, job.error, job.finished_at, job.input = Job.Status.FAILED, error, timezone.now(), None
        if handler(job).task == "compare":
            job.result = None
        job.save(update_fields=["status", "error", "finished_at", "input", "result"])
        job.batches.all().delete()
        names.update(handler(job).topics(job))
    if names:
        publish(*sorted(names))


def changed(job: Job) -> None:
    if names := handler(job).topics(job):
        publish(*names)
