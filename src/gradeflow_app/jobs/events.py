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
from gradeflow_app.jobs.models import Job, SubmissionBatch
from gradeflow_app.live.topics import notify_change

UNREADABLE_RESULTS_ERROR = "The marking service sent results that can't be read."


def _locked(job: Job) -> Job:
    if job.assessment_id is not None:
        Assessment.objects.select_for_update().get(pk=job.assessment_id)
    handler(job).lock(job)
    return Job.objects.select_for_update().get(pk=job.pk)


@transaction.atomic
def apply_started(job: Job) -> None:
    job = _locked(job)
    if job.status != Job.Status.QUEUED:
        return
    job.status, job.started_at = Job.Status.RUNNING, timezone.now()
    job.dispatched_at = job.dispatched_at or job.started_at
    job.save(update_fields=["status", "started_at", "dispatched_at"])
    notify_job_change(job)


@transaction.atomic
def apply_comparison_part(job: Job, event: dict[str, Any]) -> None:
    job = _locked(job)
    if not job.is_active or not job.input:
        return
    part = _ComparisonPart.model_validate(event)
    result = job.result or {"parts": [], "total": part.total}
    parts = result["parts"]
    if result["total"] != part.total or part.index > len(parts):
        raise ValueError("Comparison parts must arrive in order.")
    if part.index < len(parts):
        if parts[part.index] != part.text:
            raise ValueError("A comparison part cannot change.")
        return
    parts.append(part.text)
    job.result, job.progress = result, len(parts) / part.total
    job.save(update_fields=["result", "progress"])
    notify_job_change(job)


class _ComparisonPart(BaseModel, strict=True):
    """A piece of a comparison's JSON: one of `total`, from 0."""

    index: int
    total: int
    text: str

    @model_validator(mode="after")
    def _index_in_range(self) -> Self:
        if not 0 <= self.index < self.total:
            raise ValueError("Invalid comparison part.")
        return self


@transaction.atomic
def add_marked_batch(job: Job, submissions: Sequence[dict[str, Any]]) -> None:
    job = _locked(job)
    if not job.is_active or not job.input or not handler(job).is_current(job):
        return
    try:
        marked = [MarkedSubmission.model_validate(item).model_dump(mode="json") for item in submissions]
    except ValidationError:
        fail(job, UNREADABLE_RESULTS_ERROR)
        return
    if not job.batches.filter(submissions=marked).exists():
        SubmissionBatch.objects.create(job=job, submissions=marked)
        total = len(job.input["submissions"])
        Job.objects.filter(pk=job.pk).update(progress=F("progress") + len(marked) / max(total, 1))
        notify_job_change(job)


def store_test_results(job: Job, results: object) -> None:
    _store_result(job, _ROW_RESULTS_ADAPTER, results, only_if_current=True)


def store_model_choice(job: Job, choice: object) -> None:
    _store_result(job, _MODEL_CHOICE_ADAPTER, choice)


def store_model_trials(job: Job, trials: object) -> None:
    _store_result(job, MODEL_TRIALS_ADAPTER, trials)


@transaction.atomic
def _store_result(job: Job, adapter: TypeAdapter[Any], reported: object, *, only_if_current: bool = False) -> None:
    """Keep what the job reported, as `adapter` reads it, for its result; with `only_if_current`, only while its
    outcome still applies."""
    job = _locked(job)
    if not job.is_active or (only_if_current and not handler(job).is_current(job)):
        return
    try:
        job.result = adapter.dump_python(adapter.validate_python(reported), mode="json")
    except ValidationError:
        fail(job, UNREADABLE_RESULTS_ERROR)
        return
    job.save(update_fields=["result"])


@transaction.atomic
def record_progress(job: Job, event: dict[str, Any]) -> None:
    job = _locked(job)
    if not job.is_active:
        return
    try:
        progress = _Progress.model_validate(event)
    except ValidationError:
        return
    job.progress_text = f"{progress.step[:150]}: {progress.completed} of {progress.total}"
    job.save(update_fields=["progress_text"])
    notify_job_change(job)


class _Progress(BaseModel):
    completed: int
    total: int
    step: str


class _Choice(BaseModel):
    fast_model: str | None
    careful_model: str | None
    trials: list[ModelTrial]


_ROW_RESULTS_ADAPTER = TypeAdapter(list[RowResult])
_MODEL_CHOICE_ADAPTER = TypeAdapter(_Choice)
MODEL_TRIALS_ADAPTER = TypeAdapter(list[ModelTrial])


@transaction.atomic
def complete(job: Job) -> None:
    job = _locked(job)
    if not job.is_active:
        return
    job_handler = handler(job)
    if not job_handler.is_current(job):
        fail(job, "Out of date.")
        return
    try:
        outcome, result_json = _outcome_and_result(job, job_handler.task)
    except ValueError as error:
        fail(job, str(error))
        return
    if job_handler.apply_outcome is not None:
        job_handler.apply_outcome(job, outcome)
    job.result = None if job_handler.apply_outcome is not None else result_json
    job.batches.all().delete()
    job.status, job.progress, job.finished_at, job.progress_text = Job.Status.COMPLETED, 1, timezone.now(), ""
    job.input = None
    job.save()
    notify_job_change(job)


def _outcome_and_result(job: Job, task: Task) -> tuple[Any, Any]:
    """What the job's task gave, and the JSON the job keeps of it as its result. Raises `ValueError` if it doesn't
    answer what the job ran on."""
    match task:
        case "mark":
            submissions = _submissions(job)
            return submissions, [submission.model_dump(mode="json") for submission in submissions]
        case "compare":
            return _comparison(job), None
        case "test":
            return _test_results(job), job.result
        case "choose" | "trial":
            if job.result is None:
                raise ValueError("The marking service didn't say how the models did.")
            return job.result, job.result


def _submissions(job: Job) -> list[MarkedSubmission]:
    submissions = [MarkedSubmission.model_validate(item) for batch in job.batches.all() for item in batch.submissions]
    expected = {item["student_id"] for item in (job.input or {}).get("submissions", [])}
    received = {submission.student_id for submission in submissions}
    missing_outputs = any(set(job.question_keys) - submission.results.keys() for submission in submissions)
    if received != expected or len(submissions) != len(received) or missing_outputs:
        raise ValueError("The marking service returned an incomplete set of student results.")
    return submissions


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
    if comparison.texts != tuple((job.input or {}).get("texts", [])) or comparison.similarity != "meaning":
        raise ValueError("The answer comparison does not match this job's inputs.")
    return comparison


def fail(job: Job, error: str) -> None:
    fail_all([job], error)


@transaction.atomic
def fail_all(jobs: Sequence[Job], error: str) -> None:
    topics: set[str] = set()
    for candidate in sorted(
        jobs, key=lambda item: (str(item.course_id), str(item.assessment_id), str(item.marking_id), str(item.pk))
    ):
        job = _locked(candidate)
        if not job.is_active:
            continue
        job.status, job.error, job.finished_at, job.input = Job.Status.FAILED, error, timezone.now(), None
        if handler(job).task == "compare":
            job.result = None
        job.save(update_fields=["status", "error", "finished_at", "input", "result"])
        job.batches.all().delete()
        topics.update(handler(job).topics(job))
    if topics:
        notify_change(*sorted(topics))


def notify_job_change(job: Job) -> None:
    if topics := handler(job).topics(job):
        notify_change(*topics)
