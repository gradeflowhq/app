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
from gradeflow_app.jobs.events import TRIALS
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import ModelChoiceRun, ModelTestRun, ModelTry
from gradeflow_app.live.topics import topic
from gradeflow_app.model_service.endpoints import State

KINDS = (Job.Kind.MODEL_CHOICE, Job.Kind.MODEL_TEST)


@transaction.atomic
def choose(user: User, endpoint: ModelEndpoint) -> Job:
    """Try the endpoint's models and keep the best. Those in use stay until the device collects the new ones."""
    queue.replace(running(Job.Kind.MODEL_CHOICE, user, endpoint.url), queue.STOPPED)
    return queue.enqueue(
        kind=Job.Kind.MODEL_CHOICE,
        course=None,
        keys=[],
        data=ModelChoiceRun(model_endpoint=endpoint).model_dump(mode="json"),
        provenance={"url": endpoint.url},
        user=user,
    )


@transaction.atomic
def test(user: User, endpoint: ModelEndpoint, tries: list[ModelTry]) -> Job:
    """Try each model in its role on the method's examples."""
    queue.replace(running(Job.Kind.MODEL_TEST, user, endpoint.url), queue.STOPPED)
    return queue.enqueue(
        kind=Job.Kind.MODEL_TEST,
        course=None,
        keys=[],
        data=ModelTestRun(model_endpoint=endpoint, tries=tries).model_dump(mode="json"),
        provenance={"url": endpoint.url, "tries": [each.model_dump() for each in tries]},
        user=user,
    )


def stop(user: User, url: str) -> None:
    queue.replace(running(Job.Kind.MODEL_CHOICE, user, url), queue.STOPPED)


def running(kind: Job.Kind, user: User, url: str) -> QuerySet[Job]:
    """The person's jobs of the kind for the endpoint at `url` that haven't ended."""
    return Job.objects.running().filter(kind=kind, created_by=user, provenance__url=url)


def collected(user: User, state: State) -> State:
    """`state` with what the person's jobs for its endpoint found since it last collected: the models chosen
    automatically, and each trial."""
    since = datetime.fromtimestamp(state.since, UTC)
    found = Job.objects.filter(
        kind__in=KINDS,
        created_by=user,
        provenance__url=state.url,
        status=Job.Status.COMPLETED,
        finished_at__gt=since,
    ).order_by("finished_at")
    update: dict[str, Any] = {}
    trials = state.trials
    for job in found:
        assert job.finished_at is not None  # completed
        result: Any = job.result
        if job.kind == Job.Kind.MODEL_CHOICE:
            update |= {
                "decision_model": result["decision_model"] or "",
                "reasoning_model": result["reasoning_model"] or "",
                "how": "automatically",
            }
            trials = _merged(trials, TRIALS.validate_python(result["trials"]))
        else:
            trials = _merged(trials, TRIALS.validate_python(result))
        update |= {"trials": trials, "since": job.finished_at.timestamp()}
    return state.model_copy(update=update)


def failed(user: User, state: State, kind: Job.Kind) -> str:
    """Why the person's latest job of the kind for the device's endpoint failed, if it did since the device last
    collected. One they stopped didn't fail."""
    job = Job.objects.filter(kind=kind, created_by=user, provenance__url=state.url).exclude(error=queue.STOPPED).first()
    if job is None or job.status != Job.Status.FAILED or not job.finished_at:
        return ""
    return job.error if job.finished_at.timestamp() > state.since else ""


def _merged(kept: Iterable[ModelTrial], new: Iterable[ModelTrial]) -> list[ModelTrial]:
    """Each model's latest trial in each role."""
    return list({(trial.model, trial.role): trial for trial in [*kept, *new]}.values())


def _topics(job: Job) -> list[str]:
    return [topic("models", job.created_by_id)]


register(Job.Kind.MODEL_CHOICE, Handler[dict[str, Any]](task="choose", topics=_topics))
register(Job.Kind.MODEL_TEST, Handler[list[dict[str, Any]]](task="try", topics=_topics))
