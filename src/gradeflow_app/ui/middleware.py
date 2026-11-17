import json
from collections.abc import Callable, Iterable
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.contrib.auth import middleware as auth_middleware
from django.contrib.messages.storage.base import Message
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.middleware.gzip import GZipMiddleware
from django.utils import timezone

from gradeflow_app.requests import HtmxRequest

NAVIGATION_HEADERS = ("HX-Location", "HX-Redirect", "HX-Refresh")
"""Headers that have htmx load another page, which shows the messages itself."""
LIVE_REFRESH = "X-Live-Refresh"
"""Marks a refresh a page sends by itself after a change (behaviours/htmx.js). Its messages wait for the next page."""
MESSAGES_HEADER = "X-Messages"


class CompressionMiddleware(GZipMiddleware):
    """Compresses responses, except live streams: their events are a few bytes each, and a proxy may hold a
    compressed stream back."""

    def process_response(self, request: HttpRequest, response: HttpResponseBase) -> HttpResponseBase:
        if response.get("Content-Type", "").startswith("text/event-stream"):
            return response
        return super().process_response(request, response)


class LoginRequiredMiddleware(auth_middleware.LoginRequiredMiddleware):
    """Answers a signed-out in-page request with a 401, on which behaviours/htmx.js and services/requests.js go to sign
    in, rather than with the sign-in page, which htmx would swap into the page."""

    def process_view(
        self,
        request: HttpRequest,
        view_func: Callable[..., HttpResponseBase],
        view_args: tuple[Any, ...],
        view_kwargs: dict[str, Any],
    ) -> HttpResponseBase | None:
        response = super().process_view(request, view_func, view_args, view_kwargs)
        if response is not None and request.headers.get("Sec-Fetch-Mode", "navigate") != "navigate":
            return HttpResponse(status=401)
        return response


class HtmxMessagesMiddleware:
    """Sends messages added during an in-page request with its response, in an `X-Messages` header that
    behaviours/messages.js shows as toasts, rather than leaving them for whichever page loads next. Responses that go to
    another page, redirects included, keep them for it, as do live refreshes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HtmxRequest) -> HttpResponse:
        response = self.get_response(request)
        is_navigation = response.status_code in range(300, 400) or any(
            header in response for header in NAVIGATION_HEADERS
        )
        if not request.htmx or request.htmx.boosted or is_navigation or LIVE_REFRESH in request.headers:
            return response
        message_storage = messages.get_messages(request)
        if getattr(message_storage, "used", False):  # a whole page, which shows them itself
            return response
        if toast_list := toasts(message_storage):
            response[MESSAGES_HEADER] = json.dumps(toast_list)
        return response


class TimezoneMiddleware:
    """Shows times in the browser's time zone, from the `tz` cookie behaviours/timezone.js sets; UTC without it."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            timezone.activate(ZoneInfo(request.COOKIES.get("tz", "")))
        except ZoneInfoNotFoundError, ValueError:
            timezone.deactivate()
        return self.get_response(request)


def toasts(messages: Iterable[Message]) -> list[dict[str, str]]:
    """Messages as behaviours/messages.js shows them."""
    return [{"level": message.level_tag, "text": str(message)} for message in messages]
