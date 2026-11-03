"""Choosing a device's models: automatically, by a job running the core's `choose_models`, or by the person, with jobs
running `try_model` to test what they pick. Jobs keep what they found until the device collects it."""

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from gradeflow_core import ModelEndpoint
from gradeflow_core.editing import ModelTrial

from gradeflow_app.accounts.models import User
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.events import MODEL_TRIALS_ADAPTER
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import ModelChoiceRun, ModelTrialRequest, ModelTrialRun
from gradeflow_app.live.topics import topic
from gradeflow_app.model_service.endpoints import ModelServiceState

MODEL_JOB_KINDS = (Job.Kind.MODEL_CHOICE, Job.Kind.MODEL_TRIAL)


@transaction.atomic
def choose(user: User, endpoint: ModelEndpoint) -> Job:
    """Try the endpoint's models and keep the best. Those in use stay until the device collects the new ones."""
    queue.cancel_jobs(running(Job.Kind.MODEL_CHOICE, user, endpoint.url), queue.STOPPED_REASON)
    return queue.enqueue(
        kind=Job.Kind.MODEL_CHOICE,
        course=None,
        question_keys=[],
        input=ModelChoiceRun(model_endpoint=endpoint).model_dump(mode="json"),
        marked_with={"url": endpoint.url},
        user=user,
    )


@transaction.atomic
def test(user: User, endpoint: ModelEndpoint, trials: list[ModelTrialRequest]) -> Job:
    """Try each model in its role on the method's examples."""
    queue.cancel_jobs(running(Job.Kind.MODEL_TRIAL, user, endpoint.url), queue.STOPPED_REASON)
    return queue.enqueue(
        kind=Job.Kind.MODEL_TRIAL,
        course=None,
        question_keys=[],
        input=ModelTrialRun(model_endpoint=endpoint, trials=trials).model_dump(mode="json"),
        marked_with={"url": endpoint.url, "trials": [trial.model_dump() for trial in trials]},
        user=user,
    )


def stop(user: User, url: str) -> None:
    queue.cancel_jobs(running(Job.Kind.MODEL_CHOICE, user, url), queue.STOPPED_REASON)


def running(kind: Job.Kind, user: User, url: str) -> QuerySet[Job]:
    """The person's jobs of the kind for the endpoint at `url` that haven't ended."""
    return Job.objects.running().filter(kind=kind, created_by=user, marked_with__url=url)


def with_job_results(user: User, state: ModelServiceState) -> ModelServiceState:
    """`state` with what the person's jobs for its endpoint found since it last collected: the models chosen
    automatically, and each trial."""
    collected_until = datetime.fromtimestamp(state.collected_until, UTC)
    finished_jobs = Job.objects.filter(
        kind__in=MODEL_JOB_KINDS,
        created_by=user,
        marked_with__url=state.url,
        status=Job.Status.COMPLETED,
        finished_at__gt=collected_until,
    ).order_by("finished_at")
    update: dict[str, Any] = {}
    trials = state.trials
    for job in finished_jobs:
        assert job.finished_at is not None  # completed
        result: Any = job.result
        if job.kind == Job.Kind.MODEL_CHOICE:
            update |= {
                "fast_model": result["fast_model"] or "",
                "careful_model": result["careful_model"] or "",
                "chosen_by": "automatically",
            }
            trials = _latest_trials(trials, MODEL_TRIALS_ADAPTER.validate_python(result["trials"]))
        else:
            trials = _latest_trials(trials, MODEL_TRIALS_ADAPTER.validate_python(result))
        update |= {"trials": trials, "collected_until": job.finished_at.timestamp()}
    return state.model_copy(update=update)


def failure_reason(user: User, state: ModelServiceState, kind: Job.Kind) -> str:
    """Why the person's latest job of the kind for the device's endpoint failed, if it did since the device last
    collected. One they stopped didn't fail."""
    job = (
        Job.objects.filter(kind=kind, created_by=user, marked_with__url=state.url)
        .exclude(error=queue.STOPPED_REASON)
        .first()
    )
    if job is None or job.status != Job.Status.FAILED or not job.finished_at:
        return ""
    return job.error if job.finished_at.timestamp() > state.collected_until else ""


def _latest_trials(existing: Iterable[ModelTrial], new: Iterable[ModelTrial]) -> list[ModelTrial]:
    """Each model's latest trial in each role."""
    return list({(trial.model, trial.role): trial for trial in [*existing, *new]}.values())


def _topics(job: Job) -> list[str]:
    return [topic("models", job.created_by_id)]


register(Job.Kind.MODEL_CHOICE, Handler[dict[str, Any]](task="choose", topics=_topics))
register(Job.Kind.MODEL_TRIAL, Handler[list[dict[str, Any]]](task="trial", topics=_topics))
