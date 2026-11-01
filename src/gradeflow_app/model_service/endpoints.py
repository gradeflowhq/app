"""The model service a device keeps: which of the server's AI services, its token, the chosen models and what it
learned of them, all in the browser. Every change sends them in headers; the server keeps none of it."""

from typing import Annotated, Literal

from django.conf import settings
from gradeflow_core import Check, ModelEndpoint
from gradeflow_core.editing import ModelTrial
from pydantic import AfterValidator, BaseModel, Field, SecretStr, ValidationError

from gradeflow_app.requests import Request

STATE_HEADER = "X-Model-State"
TOKEN_HEADER = "X-Model-Token"


def _offered(provider: str) -> str:
    if provider not in settings.GRADEFLOW_MODEL_PROVIDERS:
        raise ValueError(f"{provider} isn't an AI service this server offers")
    return provider


class State(BaseModel):
    """What a device knows of its model service, but its token."""

    provider: Annotated[str, AfterValidator(_offered)]
    """One of `GRADEFLOW_MODEL_PROVIDERS`, the only endpoints jobs and the server send requests to."""
    decision_model: str = Field(default="", max_length=200)
    reasoning_model: str = Field(default="", max_length=200)
    answers_per_request: int = Field(default=12, ge=1, le=40)
    how: Literal["", "automatically", "myself"] = ""
    trials: list[ModelTrial] = []
    """Each model's latest trial in each role."""
    since: float = 0
    """When the newest job whose outcome it holds finished, as a timestamp."""

    @property
    def url(self) -> str:
        return settings.GRADEFLOW_MODEL_PROVIDERS[self.provider]


def state(request: Request) -> State | None:
    """The device's model service, without its token; None when it has none."""
    try:
        return State.model_validate_json(request.headers.get(STATE_HEADER, ""))
    except ValidationError:
        return None


def endpoint(request: Request, known: State | None = None) -> ModelEndpoint | None:
    """The device's model endpoint with its chosen models; None without a connection."""
    found, token = known or state(request), request.headers.get(TOKEN_HEADER, "")
    if found is None or not token:
        return None
    return ModelEndpoint(
        url=found.url,
        token=SecretStr(token),
        decision_model=found.decision_model or None,
        reasoning_model=found.reasoning_model or None,
        answers_per_request=found.answers_per_request,
    )


def asks_models(checks: list[Check]) -> bool:
    """Whether marking with the checks asks a language model."""
    return any(check.needs_model_endpoint() for check in checks)


def for_checks(checks: list[Check], endpoint: ModelEndpoint | None) -> ModelEndpoint | None:
    """`endpoint` when the checks ask a language model, so a job's input holds the token only when it must."""
    return endpoint if endpoint is not None and asks_models(checks) else None
