from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.urls import ResolverMatch
from django_htmx.middleware import HtmxDetails

from gradeflow_app.accounts.models import User


class HtmxRequest(HttpRequest):
    htmx: HtmxDetails


class Request(HtmxRequest):
    """A signed-in user's request: every view gets one but the public ones, which LoginRequiredMiddleware lets by."""

    user: User
    resolver_match: ResolverMatch


type View = Callable[..., HttpResponse]
