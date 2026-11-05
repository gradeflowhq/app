"""The settings page's AI service: the models marking asks, and testing them. The service and token arrive in headers
(`endpoints`)."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST
from gradeflow_core import GradeFlowError, ModelTrial, best_models
from gradeflow_core.editing import list_models
from pydantic import ValidationError as PydanticValidationError

from gradeflow_app.jobs.payloads import ModelTrialRequest
from gradeflow_app.model_service import choosing
from gradeflow_app.model_service.endpoints import SERVICE_HEADER, connected_service, endpoint
from gradeflow_app.model_service.models import (
    ANSWERS_PER_REQUEST_LIMITS,
    MODEL_NAME_LENGTH,
    ModelService,
    ModelTest,
)
from gradeflow_app.requests import Request

NOT_CONNECTED_ERROR = "You aren't connected to an AI service."
ROLES = ModelTest.Role.choices
SEARCHABLE_MODELS = 8
"""The most models the table lists without a search box."""


@dataclass(frozen=True)
class RoleModel:
    """The model in use in a role, and how it did when tested."""

    label: str
    model: str
    trial: ModelTrial | None


@dataclass(frozen=True)
class TrialCell:
    """A model in one role in the table: how it did when tested, and whether it is in use, the best tested so far or
    being tested."""

    role: str
    trial: ModelTrial | None
    in_use: bool
    best: bool
    testing: bool


@dataclass(frozen=True)
class ModelRow:
    model: str
    cells: list[TrialCell]


@require_POST
def models(request: Request) -> HttpResponse:
    """The models in use, as they refresh while models are tested."""
    return _models(request, connected_service(request))


@require_POST
def connect(request: Request) -> HttpResponse:
    """The card for the AI service the person just connected, or why it couldn't. Listing the service's models
    proves the token works."""
    service = connected_service(request)
    if service is None or (model_endpoint := endpoint(request, service)) is None:
        return _card(request, None, connect_error="Choose an AI service and enter its token.")
    try:
        model_names = list_models(model_endpoint)
    except GradeFlowError as error:
        return _card(request, None, connect_error=str(error), provider=request.headers.get(SERVICE_HEADER, ""))
    return _card(request, service, model_names=model_names)


@require_POST
def models_dialog(request: Request) -> HttpResponse:
    """The Models dialog: every model the endpoint lists, and how each did as the fast and the careful model."""
    service, model_names, error = connected_service(request), [], ""
    if service is None or (model_endpoint := endpoint(request, service)) is None:
        error = NOT_CONNECTED_ERROR
    else:
        try:
            model_names = list_models(model_endpoint)
        except GradeFlowError as list_error:
            error = str(list_error)
    return render(request, "model_service/chooser.html", {"error": error} | _table(request, service, model_names))


@require_POST
def table(request: Request) -> HttpResponse:
    """The Models dialog's table, as tests start and end."""
    return render(request, "model_service/table.html", _table(request, connected_service(request)))


@require_POST
def choose(request: Request) -> HttpResponse:
    """Choose automatically: use the best model tested, and test those not yet tested."""
    service = connected_service(request)
    if service is None or (model_endpoint := endpoint(request, service)) is None:
        return _models(request, service, error=NOT_CONNECTED_ERROR)
    choosing.choose(service, model_endpoint)
    return _updated(request, service)


@require_POST
def automatic(request: Request) -> HttpResponse:
    """Use the best model tested in each role, or keep those in use from now on."""
    service = connected_service(request)
    if service is None:
        return _models(request, service, error=NOT_CONNECTED_ERROR)
    if request.POST.get("automatically"):
        service.chosen_by = ModelService.ChosenBy.AUTOMATICALLY
    else:
        service.fast_model, service.careful_model = service.models_in_use()
        service.chosen_by = ModelService.ChosenBy.MYSELF
    service.save()
    return _updated(request, service)


@require_POST
def use(request: Request) -> HttpResponse:
    """Use the models picked in the table, from now on."""
    service = connected_service(request)
    if service is None:
        return _models(request, service, error=NOT_CONNECTED_ERROR)
    service.fast_model = request.POST.get("fast_model", "")[:MODEL_NAME_LENGTH]
    service.careful_model = request.POST.get("careful_model", "")[:MODEL_NAME_LENGTH]
    service.chosen_by = ModelService.ChosenBy.MYSELF
    service.save()
    return _updated(request, service)


@require_POST
def test(request: Request) -> HttpResponse:
    """Test one model in one role."""
    service = connected_service(request)
    if service is None or (model_endpoint := endpoint(request, service)) is None:
        return _models(request, service, error=NOT_CONNECTED_ERROR)
    try:
        trial = ModelTrialRequest.model_validate(
            {"model": request.POST.get("model", "")[:MODEL_NAME_LENGTH], "role": request.POST.get("role")}
        )
    except PydanticValidationError:
        return _updated(request, service)
    choosing.test(service, model_endpoint, trial)
    return _updated(request, service)


@require_POST
def answers_per_request(request: Request) -> HttpResponse:
    """Keep how many students' answers one request to the model holds."""
    service = connected_service(request)
    if service is None:
        return _models(request, service, error=NOT_CONNECTED_ERROR)
    value = request.POST.get("answers_per_request", "")
    service.answers_per_request = value  # full_clean converts it
    try:
        service.full_clean(validate_unique=False, validate_constraints=False)
    except ValidationError:
        service.refresh_from_db()
        limits = ANSWERS_PER_REQUEST_LIMITS
        error = f"Enter a number from {limits['minimum']} to {limits['maximum']}."
        return _models(request, service, answers_per_request_error=error, answers_per_request_value=value)
    service.save(update_fields=["answers_per_request"])
    return _models(request, service)


@require_POST
def stop(request: Request) -> HttpResponse:
    service = connected_service(request)
    if service is not None:
        choosing.stop(service)
    return _updated(request, service)


def _updated(request: Request, service: ModelService | None) -> HttpResponse:
    """The card's models after a change; from the Models dialog, its table too, which lists the models it was sent
    with."""
    if "listed" not in request.POST:
        return _models(request, service)
    card_html = render_to_string(
        "model_service/card.html#models", _models_context(request, service) | {"out_of_band": True}, request
    )
    return render(request, "model_service/table.html", _table(request, service) | {"card_html": card_html})


def _table(request: Request, service: ModelService | None, model_names: Sequence[str] = ()) -> dict[str, Any]:
    """The table of `model_names` and every other model tested, those in use first, then those that passed, then the
    rest; or of the models the dialog listed, in its order, so rows stay put while it is open."""
    if service is None:
        return {"service": None, "rows": [], "searchable": False}
    listed = list(model_names) or [name[:MODEL_NAME_LENGTH] for name in request.POST.getlist("listed")]
    tests = list(service.tests.all())
    trials = {(test.model, test.role): test.trial() for test in tests if test.seconds is not None}
    best = dict(zip(ModelTest.Role.values, best_models(trials.values()), strict=True))
    in_use = dict(zip(ModelTest.Role.values, service.models_in_use(), strict=True))
    testing = {(test.model, test.role) for test in choosing.being_tested(service)}
    rows = [
        ModelRow(
            model,
            [
                TrialCell(
                    role,
                    trials.get((model, role)),
                    model == in_use[role],
                    model == best[role],
                    (model, role) in testing,
                )
                for role, _ in ROLES
            ],
        )
        for model in dict.fromkeys([*listed, *(test.model for test in tests)])
    ]
    if model_names:
        rows.sort(
            key=lambda row: (
                not any(cell.in_use for cell in row.cells),
                not any(cell.trial and cell.trial.failure is None for cell in row.cells),
            )
        )
    return {
        "service": service,
        "rows": rows,
        "searchable": len(rows) > SEARCHABLE_MODELS,
        "test_failure": service.trial_failure,
    } | _choice_progress(service)


def _choice_progress(service: ModelService) -> dict[str, Any]:
    """The automatic choice running for the service: how many tests it has finished, and the one under way."""
    if (choice := choosing.running_choice(service)) is None:
        return {"choosing_job": None}
    run, state = choice
    tests = service.tests.filter(run_id=run.id)
    return {
        "choosing_job": state,
        "tests_done": tests.filter(testing=False).count(),
        "testing_now": tests.filter(testing=True).first(),
    }


def _models_context(
    request: Request, service: ModelService | None, model_names: list[str] | None = None
) -> dict[str, Any]:
    """What the card shows of the person's models: each role's, the choice running for them, or, before any are
    chosen, how many models choosing automatically tests."""
    context: dict[str, Any] = {
        "service": service,
        "models_loaded": True,
        "answers_per_request_limits": ANSWERS_PER_REQUEST_LIMITS,
    }
    if service is None:
        return context
    trials: dict[tuple[str, str], ModelTrial] = {(trial.model, trial.role): trial for trial in service.trials()}
    in_use = dict(zip(ModelTest.Role.values, service.models_in_use(), strict=True))
    context |= _choice_progress(service) | {
        "roles": [RoleModel(label, in_use[role], trials.get((in_use[role], role))) for role, label in ROLES],
        "any_in_use": any(in_use.values()),
        "failure_reason": service.choice_failure,
    }
    if not context["any_in_use"] and not context["choosing_job"]:
        context["model_count"] = len(model_names) if model_names is not None else _model_count(request, service)
    return context


def _model_count(request: Request, service: ModelService) -> int | None:
    model_endpoint = endpoint(request, service)
    try:
        return len(list_models(model_endpoint)) if model_endpoint else None
    except GradeFlowError:
        return None


def _card(
    request: Request,
    service: ModelService | None,
    connect_error: str = "",
    provider: str = "",
    model_names: list[str] | None = None,
) -> HttpResponse:
    context = _models_context(request, service, model_names) | {
        "connect_error": connect_error,
        "provider": provider,
        "providers": settings.GRADEFLOW_MODEL_PROVIDERS,
    }
    return render(request, "model_service/card.html#card", context)


def _models(
    request: Request,
    service: ModelService | None,
    error: str = "",
    answers_per_request_error: str = "",
    answers_per_request_value: str = "",
) -> HttpResponse:
    context = _models_context(request, service) | {
        "error": error,
        "answers_per_request_error": answers_per_request_error,
        "answers_per_request_value": answers_per_request_value,
    }
    return render(request, "model_service/card.html#models", context)
