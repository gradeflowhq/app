"""Testing a person's models: automatically, by a job running the core's `try_models` on those not yet tested, or one
at a time, by jobs running `try_model`. Each test is kept as it starts and ends."""

from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from gradeflow_core import ModelEndpoint, ModelTrial

from gradeflow_app.jobs import queue
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import ModelChoiceRun, ModelTrialRequest, ModelTrialRun
from gradeflow_app.live.topics import topic
from gradeflow_app.model_service.models import ModelService, ModelTest


@transaction.atomic
def choose(service: ModelService, endpoint: ModelEndpoint) -> None:
    """Use the best models tested, and start testing the rest unless that is already running."""
    service.chosen_by = ModelService.ChosenBy.AUTOMATICALLY
    service.save(update_fields=["chosen_by"])
    if running(Job.Kind.MODEL_CHOICE, service).exists():
        return
    queue.enqueue(
        kind=Job.Kind.MODEL_CHOICE,
        course=None,
        question_keys=[],
        input=ModelChoiceRun(model_endpoint=endpoint, tried=service.trials()).model_dump(mode="json"),
        marked_with={"url": service.url},
        user=service.user,
    )


@transaction.atomic
def test(service: ModelService, endpoint: ModelEndpoint, trial: ModelTrialRequest) -> None:
    """Test one model in one role."""
    job = queue.enqueue(
        kind=Job.Kind.MODEL_TRIAL,
        course=None,
        question_keys=[],
        input=ModelTrialRun(model_endpoint=endpoint, trials=[trial]).model_dump(mode="json"),
        marked_with={"url": service.url},
        user=service.user,
    )
    _start_test(service, trial.model, trial.role, job)


def stop(service: ModelService) -> None:
    queue.cancel_jobs(running(Job.Kind.MODEL_CHOICE, service), queue.STOPPED_REASON)


def running(kind: Job.Kind, service: ModelService) -> QuerySet[Job]:
    """The person's jobs of the kind for the service that haven't ended."""
    return Job.objects.running().filter(kind=kind, created_by=service.user, marked_with__url=service.url)


def being_tested(service: ModelService) -> QuerySet[ModelTest]:
    """Tests under way, by jobs still running."""
    return service.tests.filter(testing=True, job__in=Job.objects.running())


def failure_reason(service: ModelService, kind: Job.Kind) -> str:
    """Why the last of the person's jobs of the kind for the service to end failed, if it did; stopping one isn't
    failing."""
    job = (
        Job.objects.filter(kind=kind, created_by=service.user, marked_with__url=service.url)
        .exclude(finished_at=None)
        .exclude(error=queue.STOPPED_REASON)
        .order_by("-finished_at")
        .first()
    )
    return job.error if job is not None and job.status == Job.Status.FAILED else ""


def _start_test(service: ModelService, model: str, role: str, job: Job) -> None:
    """Mark the model as being tested in the role, keeping how it did last time until the job reports again."""
    ModelTest.objects.update_or_create(service=service, model=model, role=role, defaults={"job": job, "testing": True})


def _apply_event(job: Job, event: dict[str, Any]) -> None:
    """A test starting (`trying`) or ending (`tried`)."""
    service = ModelService.objects.get(user=job.created_by, url=job.marked_with["url"])
    match event.get("event"):
        case "trying":
            _start_test(service, str(event["model"]), ModelTest.Role(event["role"]), job)
        case "tried":
            trial = ModelTrial.model_validate(event["trial"])
            ModelTest.objects.update_or_create(
                service=service,
                model=trial.model,
                role=trial.role,
                defaults=trial.model_dump(exclude={"model", "role"})
                | {"failure": trial.failure or "", "job": job, "testing": False},
            )
        case _:
            raise ValueError("Unknown job event.")


def _topics(job: Job) -> list[str]:
    return [topic("models", job.created_by_id)]


register(Job.Kind.MODEL_CHOICE, Handler(task="choose", topics=_topics, apply_event=_apply_event))
register(Job.Kind.MODEL_TRIAL, Handler(task="trial", topics=_topics, apply_event=_apply_event))
