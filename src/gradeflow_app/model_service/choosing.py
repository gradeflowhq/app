"""Testing a person's models: automatically, by a run of the core's `try_models` on those not yet tested, or one at
a time, by runs of `try_model`. Each test is kept as it starts and ends."""

from typing import Any

from django.db import transaction
from django.db.models import QuerySet
from gradeflow_core import ModelEndpoint, ModelTrial

from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import Handler, register
from gradeflow_app.jobs.payloads import ModelChoiceRun, ModelTrialRequest, ModelTrialRun
from gradeflow_app.jobs.runner import Run
from gradeflow_app.jobs.runs import RunState
from gradeflow_app.live.topics import topic
from gradeflow_app.model_service.models import ModelService, ModelTest

MODEL_CHOICE = "model-choice"
MODEL_TRIAL = "model-trial"


@transaction.atomic
def choose(service: ModelService, endpoint: ModelEndpoint) -> None:
    """Use the best models tested, and start testing the rest unless that is already running."""
    service.chosen_by = ModelService.ChosenBy.AUTOMATICALLY
    service.choice_failure = ""
    service.save(update_fields=["chosen_by", "choice_failure"])
    if not runs.active(MODEL_CHOICE, service.pk):
        runs.start(Run(MODEL_CHOICE, str(service.pk)), endpoint)


@transaction.atomic
def test(service: ModelService, endpoint: ModelEndpoint, trial: ModelTrialRequest) -> None:
    """Test one model in one role."""
    model_test, _ = ModelTest.objects.get_or_create(service=service, model=trial.model, role=trial.role)
    run = Run(MODEL_TRIAL, str(model_test.pk))
    _start_test(model_test, run)
    ModelService.objects.filter(pk=service.pk).update(trial_failure="")
    runs.start(run, endpoint)


def stop(service: ModelService) -> None:
    runs.stop(MODEL_CHOICE, service.pk)


def running_choice(service: ModelService) -> tuple[Run, RunState] | None:
    """The automatic choice running for the service, if one is."""
    return next(iter(runs.active(MODEL_CHOICE, service.pk)), None)


def being_tested(service: ModelService) -> QuerySet[ModelTest]:
    """Tests under way, by runs that haven't ended."""
    active = [run.id for run, _ in [*runs.active(MODEL_CHOICE, service.pk), *runs.active(MODEL_TRIAL)]]
    return service.tests.filter(testing=True, run_id__in=active)


def _start_test(model_test: ModelTest, run: Run) -> None:
    """Mark the model as being tested in its role, keeping how it did last time until `run` reports again."""
    model_test.run_id, model_test.testing = run.id, True
    model_test.save(update_fields=["run_id", "testing"])


def _service(run: Run) -> ModelService | None:
    if run.kind == MODEL_CHOICE:
        return ModelService.objects.filter(pk=run.owner).first()
    return ModelService.objects.filter(tests__pk=run.owner).first()


def _choice_input(run: Run) -> ModelChoiceRun | None:
    service = _service(run)
    return ModelChoiceRun(tried=service.trials()) if service is not None else None


def _trial_input(run: Run) -> ModelTrialRun | None:
    model_test = ModelTest.objects.filter(pk=run.owner, run_id=run.id).first()
    if model_test is None:
        return None
    return ModelTrialRun(
        trials=[ModelTrialRequest.model_validate({"model": model_test.model, "role": model_test.role})]
    )


def _apply_event(run: Run, event: dict[str, Any]) -> None:
    """A test starting (`trying`) or ending (`tried`)."""
    service = _service(run)
    assert service is not None
    match event.get("event"):
        case "trying":
            model_test, _ = ModelTest.objects.get_or_create(
                service=service, model=str(event["model"]), role=ModelTest.Role(event["role"])
            )
            _start_test(model_test, run)
        case "tried":
            trial = ModelTrial.model_validate(event["trial"])
            ModelTest.objects.update_or_create(
                service=service,
                model=trial.model,
                role=trial.role,
                defaults=trial.model_dump(exclude={"model", "role"})
                | {"failure": trial.failure or "", "run_id": run.id, "testing": False},
            )
        case _:
            raise ValueError("Unknown job event.")


def _finish(run: Run, outcome: Any) -> None:
    ModelTest.objects.filter(run_id=run.id).update(testing=False)


def _fail(run: Run, error: str) -> None:
    ModelTest.objects.filter(run_id=run.id).update(testing=False)
    if (service := _service(run)) is not None:
        field = "choice_failure" if run.kind == MODEL_CHOICE else "trial_failure"
        ModelService.objects.filter(pk=service.pk).update(**{field: error})


def _is_current(run: Run) -> bool:
    return _service(run) is not None


def _topics(run: Run) -> list[str]:
    service = _service(run)
    return [topic("models", service.user_id)] if service is not None else []


register(
    MODEL_CHOICE,
    Handler(
        task="choose",
        topics=_topics,
        input=_choice_input,
        is_current=_is_current,
        finish=_finish,
        fail=_fail,
        apply_event=_apply_event,
    ),
)
register(
    MODEL_TRIAL,
    Handler(
        task="trial",
        topics=_topics,
        input=_trial_input,
        is_current=_is_current,
        finish=_finish,
        fail=_fail,
        apply_event=_apply_event,
    ),
)
