"""The AI service and token a request sends in its headers. The browser decrypts them from the user's secret."""

from django.conf import settings
from gradeflow_core import ModelEndpoint
from pydantic import SecretStr

from gradeflow_app.model_service.models import ModelService
from gradeflow_app.requests import Request

SERVICE_HEADER = "X-Model-Service"
TOKEN_HEADER = "X-Model-Token"


def connected_service(request: Request) -> ModelService | None:
    """The person's use of the AI service the request names; None when it names none the server offers."""
    url = settings.GRADEFLOW_MODEL_PROVIDERS.get(request.headers.get(SERVICE_HEADER, ""))
    if url is None:
        return None
    return ModelService.objects.get_or_create(user=request.user, url=url)[0]


def endpoint(request: Request, service: ModelService | None = None) -> ModelEndpoint | None:
    """The request's model endpoint with the models in use; None without a connection."""
    service, token = service or connected_service(request), request.headers.get(TOKEN_HEADER, "")
    if service is None or not token:
        return None
    fast_model, careful_model = service.models_in_use()
    return ModelEndpoint(
        url=service.url,
        api_key=SecretStr(token),
        fast_model=fast_model or None,
        careful_model=careful_model or None,
        answers_per_request=service.answers_per_request,
    )
