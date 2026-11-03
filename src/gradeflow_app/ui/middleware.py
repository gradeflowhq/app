import json
from collections.abc import Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.contrib.messages.storage.base import Message
from django.http import HttpRequest, HttpResponse
from django.http.response import HttpResponseBase
from django.middleware.gzip import GZipMiddleware
from django.utils import timezone

from gradeflow_app.requests import HtmxRequest

NAVIGATION_HEADERS = ("HX-Location", "HX-Redirect", "HX-Refresh")
"""Headers that have htmx load another page, which shows the messages itself."""
LIVE_REFRESH = "X-Live-Refresh"
"""Marks a request a page sends by itself as something changed (htmx.js): the page may be on its way to another."""


class CompressionMiddleware(GZipMiddleware):
    """Compresses responses, except live streams: their events are a few bytes each, and a proxy may hold a
    compressed stream back."""

    def process_response(self, request: HttpRequest, response: HttpResponseBase) -> HttpResponseBase:
        if response.get("Content-Type", "").startswith("text/event-stream"):
            return response
        return super().process_response(request, response)


class HtmxMessagesMiddleware:
    """Sends messages added during an in-page request with its response, in an `X-Messages` header that htmx.js shows
    as toasts, rather than leaving them for whichever page loads next. Responses that go to another page, redirects
    included, keep them for it, as do live refreshes."""

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
            response["X-Messages"] = json.dumps(toast_list)
        return response


class TimezoneMiddleware:
    """Shows times in the time zone of the browser, which htmx.js keeps in a `tz` cookie; UTC until it does."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        try:
            timezone.activate(ZoneInfo(request.COOKIES.get("tz", "")))
        except ZoneInfoNotFoundError, ValueError:
            timezone.deactivate()
        return self.get_response(request)


def toasts(messages: Iterable[Message]) -> list[dict[str, str]]:
    """Messages as toasts.js shows them."""
    return [{"level": message.level_tag, "text": str(message)} for message in messages]
