"""The settings page's model service. The device sends all it knows of the service with each request, as
`model_state`, and keeps what the response's `X-Model-State` says it now knows."""

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
from gradeflow_app.jobs.payloads import ModelTry
from gradeflow_app.model_service import choosing
from gradeflow_app.model_service.endpoints import STATE_HEADER, State, endpoint
from gradeflow_app.requests import Request

DISCONNECTED = "This device isn't connected to an AI service."
WAYS = (
    (
        "automatically",
        "sparkles",
        "Automatically",
        "Tries each model on sample answers and keeps the best. About 10 minutes; you can leave the page.",
    ),
    ("myself", "adjustments-horizontal", "Myself", "Pick the models, and test them before using them."),
)
ROLES = (("decision", "Fast model"), ("reasoning", "Careful model"))


@require_POST
def models(request: Request) -> HttpResponse:
    """The chosen models, as they refresh while a choice runs."""
    return _models(request, _state(request))


@require_POST
def connect(request: Request) -> HttpResponse:
    """The card for the AI service this device just connected, or why it couldn't."""
    state = _state(request)
    if state is None or endpoint(request, state) is None:
        return _card(request, None, refusal="Choose an AI service and enter its token.")
    return _card(request, state)


@require_POST
def chooser(request: Request) -> HttpResponse:
    """The Choose models dialog, with the endpoint's models to pick from."""
    state, names, problem = _state(request), [], ""
    if state is None or (found := endpoint(request, state)) is None:
        problem = DISCONNECTED
    else:
        try:
            names = list_models(found)
        except GradeFlowError as error:
            problem = str(error)
    running = choosing.running(Job.Kind.MODEL_CHOICE, request.user, state.url).first() if state else None
    how = "automatically" if running or state is None else state.how or "automatically"
    picked = {"decision": state.decision_model, "reasoning": state.reasoning_model} if state else {}
    context = {"ways": WAYS, "how": how, "names": names, "problem": problem, "state": state, "running": running}
    return _kept(render(request, "model_service/chooser.html", context | _status(request, state, picked)), state)


@require_POST
def choose(request: Request) -> HttpResponse:
    """Use the models picked, or start choosing automatically, for the endpoint this device is connected to."""
    state = _state(request)
    if state is None or (found := endpoint(request, state)) is None:
        return _models(request, state, problem=DISCONNECTED)
    if request.POST.get("how") != "myself":
        if not choosing.running(Job.Kind.MODEL_CHOICE, request.user, state.url).exists():
            choosing.choose(request.user, found)
        return _models(request, state)
    picked = _picked(request.POST)
    if not picked["decision"]:
        return _models(request, state, problem="Choose a fast model.")
    update = {"decision_model": picked["decision"], "reasoning_model": picked["reasoning"], "how": "myself"}
    return _models(request, state.model_copy(update=update))


@require_POST
def answers_per_request(request: Request) -> HttpResponse:
    """Keep how many students' answers one request to the model holds."""
    state = _state(request)
    if state is None:
        return _models(request, state, problem=DISCONNECTED)
    try:
        changed = State.model_validate(
            state.model_dump() | {"answers_per_request": request.POST.get("answers_per_request")}
        )
    except ValidationError:
        return _models(request, state, answers_problem="Enter a number from 1 to 40.")
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
    if state is None or (found := endpoint(request, state)) is None:
        return _rendered_status(request, state, picked, problem=DISCONNECTED)
    tries = [ModelTry.model_validate({"model": model, "role": role}) for role, model in picked.items() if model]
    if tries:
        choosing.test(request.user, found, tries)
    return _rendered_status(request, state, picked)


@require_POST
def status(request: Request) -> HttpResponse:
    """How the models picked in the dialog did when last tried, as the picks change and tests finish."""
    return _rendered_status(request, _state(request), _picked(request.POST))


@dataclass(frozen=True)
class Pick:
    """A model picked for a role in the dialog, and what is known of it."""

    role: str
    label: str
    model: str
    tried: ModelTrial | None
    testing: bool


def _state(request: Request) -> State | None:
    """What the device knows of its model service, with what its jobs found since it last asked."""
    try:
        state = State.model_validate_json(request.POST.get("model_state", ""))
    except ValidationError:
        return None
    return choosing.collected(request.user, state)


def _kept(response: HttpResponse, state: State | None) -> HttpResponse:
    if state is not None:
        response[STATE_HEADER] = state.model_dump_json()
    return response


def _picked(data: QueryDict) -> dict[str, str]:
    return {role: data.get(f"{role}_model", "")[:200] for role, _ in ROLES}


def _status(request: Request, state: State | None, picked: dict[str, str], problem: str = "") -> dict[str, Any]:
    trials: dict[tuple[str, str], ModelTrial] = {(t.model, t.role): t for t in (state.trials if state else [])}
    testing = choosing.running(Job.Kind.MODEL_TEST, request.user, state.url) if state else Job.objects.none()
    under_test = {(each["model"], each["role"]) for job in testing for each in job.provenance.get("tries", [])}
    picks = [
        Pick(role, label, model, trials.get((model, role)), (model, role) in under_test)
        for role, label in ROLES
        if (model := picked.get(role))
    ]
    failed = choosing.failed(request.user, state, Job.Kind.MODEL_TEST) if state else ""
    return {
        "picks": picks,
        "status_problem": problem or (f"Couldn't test: {failed}" if failed else ""),
        "compared": compared(state),
        "testing": bool(under_test),
    }


def _rendered_status(request: Request, state: State | None, picked: dict[str, str], problem: str = "") -> HttpResponse:
    return _kept(render(request, "model_service/status.html", _status(request, state, picked, problem)), state)


Compared = tuple[str, list[tuple[ModelTrial | None, bool]]]


def compared(state: State | None) -> list[Compared]:
    """Every model tried, a row each with its trials as the fast and the careful model and whether it is in use as
    each: those in use first, then those that passed, then the rest."""
    if state is None:
        return []
    in_use = {"decision": state.decision_model, "reasoning": state.reasoning_model}
    by_model: dict[str, dict[str, ModelTrial]] = {}
    for trial in state.trials:
        by_model.setdefault(trial.model, {})[trial.role] = trial
    rows = [
        (model, [(roles.get(role), role in roles and model == in_use[role]) for role, _ in ROLES])
        for model, roles in by_model.items()
    ]

    def rank(row: Compared) -> tuple[bool, bool]:
        cells = row[1]
        return (not any(chosen for _, chosen in cells), not any(trial and trial.failure is None for trial, _ in cells))

    return sorted(rows, key=rank)


def _context(request: Request, state: State | None) -> dict[str, Any]:
    """What the card shows of the device's model service: its models, or the choice running for them."""
    if state is None:
        return {"state": None, "running": None, "failed": ""}
    return {
        "state": state,
        "running": choosing.running(Job.Kind.MODEL_CHOICE, request.user, state.url).first(),
        "failed": choosing.failed(request.user, state, Job.Kind.MODEL_CHOICE),
    }


def _card(request: Request, state: State | None, refusal: str = "") -> HttpResponse:
    context = _context(request, state) | {
        "loaded": True,
        "refusal": refusal,
        "providers": settings.GRADEFLOW_MODEL_PROVIDERS,
    }
    return _kept(render(request, "model_service/card.html#card", context), state)


def _models(request: Request, state: State | None, problem: str = "", answers_problem: str = "") -> HttpResponse:
    context = _context(request, state) | {"loaded": True, "problem": problem, "answers_problem": answers_problem}
    return _kept(render(request, "model_service/card.html#models", context), state)
