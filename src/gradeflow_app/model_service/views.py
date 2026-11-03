"""The settings page's model service. The device sends all it knows of the service with each request, as
`model_state`, and keeps what the response's `X-Model-ModelServiceState` says it now knows."""

from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.http import HttpResponse, QueryDict
from django.shortcuts import render
from django.views.decorators.http import require_POST
from gradeflow_core import GradeFlowError
from gradeflow_core.editing import ModelTrial, list_models
from pydantic import ValidationError

from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import ModelTrialRequest
from gradeflow_app.model_service import choosing
from gradeflow_app.model_service.endpoints import STATE_HEADER, ModelServiceState, endpoint
from gradeflow_app.requests import Request

NOT_CONNECTED_ERROR = "This device isn't connected to an AI service."
CHOICE_MODES = (
    (
        "automatically",
        "sparkles",
        "Automatically",
        "Tries each model on sample answers and keeps the best. About 10 minutes; you can leave the page.",
    ),
    ("myself", "adjustments-horizontal", "Pick them myself", "Pick the models, and test them before using them."),
)
ROLES = (("fast", "Fast model"), ("careful", "Careful model"))


@require_POST
def models(request: Request) -> HttpResponse:
    """The chosen models, as they refresh while a choice runs."""
    return _models(request, _state(request))


@require_POST
def connect(request: Request) -> HttpResponse:
    """The card for the AI service this device just connected, or why it couldn't. Listing the service's models
    proves the token works."""
    state = _state(request)
    if state is None or (model_endpoint := endpoint(request, state)) is None:
        return _card(request, None, connect_error="Choose an AI service and enter its token.")
    try:
        list_models(model_endpoint)
    except GradeFlowError as error:
        return _card(request, None, connect_error=str(error), provider=state.provider)
    return _card(request, state)


@require_POST
def choose_dialog(request: Request) -> HttpResponse:
    """The Choose models dialog, with the endpoint's models to pick from."""
    state, model_names, error = _state(request), [], ""
    if state is None or (model_endpoint := endpoint(request, state)) is None:
        error = NOT_CONNECTED_ERROR
    else:
        try:
            model_names = list_models(model_endpoint)
        except GradeFlowError as list_error:
            error = str(list_error)
    choosing_job = choosing.running(Job.Kind.MODEL_CHOICE, request.user, state.url).first() if state else None
    chosen_by = "automatically" if choosing_job or state is None else state.chosen_by or "automatically"
    picked = {"fast": state.fast_model, "careful": state.careful_model} if state else {}
    context = {
        "choice_modes": CHOICE_MODES,
        "chosen_by": chosen_by,
        "model_names": model_names,
        "error": error,
        "state": state,
        "choosing_job": choosing_job,
    }
    response = render(request, "model_service/chooser.html", context | _status(request, state, picked))
    return _with_state_header(response, state)


@require_POST
def choose(request: Request) -> HttpResponse:
    """Use the models picked, or start choosing automatically, for the endpoint this device is connected to."""
    state = _state(request)
    if state is None or (model_endpoint := endpoint(request, state)) is None:
        return _models(request, state, error=NOT_CONNECTED_ERROR)
    if request.POST.get("chosen_by") != "myself":
        if not choosing.running(Job.Kind.MODEL_CHOICE, request.user, state.url).exists():
            choosing.choose(request.user, model_endpoint)
        return _models(request, state)
    picked = _picked(request.POST)
    if not picked["fast"]:
        return _models(request, state, error="Choose a fast model.")
    update = {"fast_model": picked["fast"], "careful_model": picked["careful"], "chosen_by": "myself"}
    return _models(request, state.model_copy(update=update))


@require_POST
def answers_per_request(request: Request) -> HttpResponse:
    """Keep how many students' answers one request to the model holds."""
    state = _state(request)
    if state is None:
        return _models(request, state, error=NOT_CONNECTED_ERROR)
    try:
        changed = ModelServiceState.model_validate(
            state.model_dump() | {"answers_per_request": request.POST.get("answers_per_request")}
        )
    except ValidationError:
        return _models(request, state, answers_per_request_error="Enter a number from 1 to 40.")
    return _models(request, changed)


@require_POST
def stop(request: Request) -> HttpResponse:
    state = _state(request)
    if state is not None:
        choosing.stop(request.user, state.url)
    return _models(request, state)


@require_POST
def test(request: Request) -> HttpResponse:
    """Try the models picked in the dialog, each in its role."""
    state, picked = _state(request), _picked(request.POST)
    if state is None or (model_endpoint := endpoint(request, state)) is None:
        return _rendered_status(request, state, picked, error=NOT_CONNECTED_ERROR)
    trials = [
        ModelTrialRequest.model_validate({"model": model, "role": role}) for role, model in picked.items() if model
    ]
    if trials:
        choosing.test(request.user, model_endpoint, trials)
    return _rendered_status(request, state, picked)


@require_POST
def status(request: Request) -> HttpResponse:
    """How the models picked in the dialog did when last tried, as the picks change and tests finish."""
    return _rendered_status(request, _state(request), _picked(request.POST))


@dataclass(frozen=True)
class PickedModel:
    """A model picked for a role in the dialog, and what is known of it."""

    role: str
    label: str
    model: str
    latest_trial: ModelTrial | None
    is_testing: bool


def _state(request: Request) -> ModelServiceState | None:
    """What the device knows of its model service, with what its jobs found since it last asked."""
    try:
        state = ModelServiceState.model_validate_json(request.POST.get("model_state", ""))
    except ValidationError:
        return None
    return choosing.with_job_results(request.user, state)


def _with_state_header(response: HttpResponse, state: ModelServiceState | None) -> HttpResponse:
    if state is not None:
        response[STATE_HEADER] = state.model_dump_json()
    return response


def _picked(data: QueryDict) -> dict[str, str]:
    return {role: data.get(f"{role}_model", "")[:200] for role, _ in ROLES}


def _status(
    request: Request, state: ModelServiceState | None, picked: dict[str, str], error: str = ""
) -> dict[str, Any]:
    trials: dict[tuple[str, str], ModelTrial] = {
        (trial.model, trial.role): trial for trial in (state.trials if state else [])
    }
    testing = choosing.running(Job.Kind.MODEL_TRIAL, request.user, state.url) if state else Job.objects.none()
    under_test = {(trial["model"], trial["role"]) for job in testing for trial in job.marked_with.get("trials", [])}
    picks = [
        PickedModel(role, label, model, trials.get((model, role)), (model, role) in under_test)
        for role, label in ROLES
        if (model := picked.get(role))
    ]
    failure_reason = choosing.failure_reason(request.user, state, Job.Kind.MODEL_TRIAL) if state else ""
    return {
        "picks": picks,
        "test_error": error or (f"Couldn't test: {failure_reason}" if failure_reason else ""),
        "trial_rows": trial_rows(state),
        "testing": bool(under_test),
    }


def _rendered_status(
    request: Request, state: ModelServiceState | None, picked: dict[str, str], error: str = ""
) -> HttpResponse:
    response = render(request, "model_service/status.html", _status(request, state, picked, error))
    return _with_state_header(response, state)


TrialRow = tuple[str, list[tuple[ModelTrial | None, bool]]]


def trial_rows(state: ModelServiceState | None) -> list[TrialRow]:
    """Every model tried, a row each with its trials as the fast and the careful model and whether it is in use as
    each: those in use first, then those that passed, then the rest."""
    if state is None:
        return []
    models_in_use = {"fast": state.fast_model, "careful": state.careful_model}
    by_model: dict[str, dict[str, ModelTrial]] = {}
    for trial in state.trials:
        by_model.setdefault(trial.model, {})[trial.role] = trial
    rows = [
        (model, [(roles.get(role), role in roles and model == models_in_use[role]) for role, _ in ROLES])
        for model, roles in by_model.items()
    ]

    def rank(row: TrialRow) -> tuple[bool, bool]:
        role_trials = row[1]
        return (
            not any(in_use for _, in_use in role_trials),
            not any(trial and trial.failure is None for trial, _ in role_trials),
        )

    return sorted(rows, key=rank)


def _context(request: Request, state: ModelServiceState | None) -> dict[str, Any]:
    """What the card shows of the device's model service: its models, or the choice running for them."""
    if state is None:
        return {"state": None, "choosing_job": None, "failure_reason": ""}
    return {
        "state": state,
        "choosing_job": choosing.running(Job.Kind.MODEL_CHOICE, request.user, state.url).first(),
        "failure_reason": choosing.failure_reason(request.user, state, Job.Kind.MODEL_CHOICE),
    }


def _card(
    request: Request, state: ModelServiceState | None, connect_error: str = "", provider: str = ""
) -> HttpResponse:
    context = _context(request, state) | {
        "models_loaded": True,
        "connect_error": connect_error,
        "provider": provider,
        "providers": settings.GRADEFLOW_MODEL_PROVIDERS,
    }
    return _with_state_header(render(request, "model_service/card.html#card", context), state)


def _models(
    request: Request, state: ModelServiceState | None, error: str = "", answers_per_request_error: str = ""
) -> HttpResponse:
    context = _context(request, state) | {
        "models_loaded": True,
        "error": error,
        "answers_per_request_error": answers_per_request_error,
    }
    return _with_state_header(render(request, "model_service/card.html#models", context), state)
