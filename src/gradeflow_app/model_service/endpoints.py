"""The model service a device keeps: which of the server's AI services, its token, the chosen models and what it
learned of them, all in the browser. Every change sends them in headers; the server keeps none of it."""

from typing import Annotated, Literal

from django.conf import settings
from gradeflow_core import ModelEndpoint
from gradeflow_core.editing import ModelTrial
from pydantic import AfterValidator, BaseModel, Field, SecretStr, ValidationError

from gradeflow_app.requests import Request

STATE_HEADER = "X-Model-ModelServiceState"
TOKEN_HEADER = "X-Model-Token"


def _check_offered(provider: str) -> str:
    if provider not in settings.GRADEFLOW_MODEL_PROVIDERS:
        raise ValueError(f"{provider} isn't an AI service this server offers")
    return provider


class ModelServiceState(BaseModel):
    """What a device knows of its model service, but its token."""

    provider: Annotated[str, AfterValidator(_check_offered)]
    """One of `GRADEFLOW_MODEL_PROVIDERS`, the only endpoints jobs and the server send requests to."""
    fast_model: str = Field(default="", max_length=200)
    careful_model: str = Field(default="", max_length=200)
    answers_per_request: int = Field(default=12, ge=1, le=40)
    chosen_by: Literal["", "automatically", "myself"] = ""
    trials: list[ModelTrial] = []
    """Each model's latest trial in each role."""
    collected_until: float = 0
    """When the newest job whose outcome it holds finished, as a timestamp."""

    @property
    def url(self) -> str:
        return settings.GRADEFLOW_MODEL_PROVIDERS[self.provider]


def state(request: Request) -> ModelServiceState | None:
    """The device's model service, without its token; None when it has none."""
    try:
        return ModelServiceState.model_validate_json(request.headers.get(STATE_HEADER, ""))
    except ValidationError:
        return None


def endpoint(request: Request, known_state: ModelServiceState | None = None) -> ModelEndpoint | None:
    """The device's model endpoint with its chosen models; None without a connection."""
    device_state, token = known_state or state(request), request.headers.get(TOKEN_HEADER, "")
    if device_state is None or not token:
        return None
    return ModelEndpoint(
        url=device_state.url,
        token=SecretStr(token),
        fast_model=device_state.fast_model or None,
        careful_model=device_state.careful_model or None,
        answers_per_request=device_state.answers_per_request,
    )
