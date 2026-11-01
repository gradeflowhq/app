import json
from collections.abc import Callable, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib import messages
from django.contrib.messages.storage.base import Message
from django.http import HttpRequest, HttpResponse
from django.utils import timezone

from gradeflow_app.requests import HtmxRequest

NAVIGATING = ("HX-Location", "HX-Redirect", "HX-Refresh")
"""Headers that have htmx load another page, which shows the messages itself."""
LIVE_REFRESH = "X-Live-Refresh"
"""Marks a request a page sends by itself as something changed (htmx.js): the page may be on its way to another."""


class HtmxMessagesMiddleware:
    """Sends messages added during an in-page request with its response, in an `X-Messages` header that htmx.js shows
    as toasts, rather than leaving them for whichever page loads next. Responses that go to another page, redirects
    included, keep them for it, as do live refreshes."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HtmxRequest) -> HttpResponse:
        response = self.get_response(request)
        navigating = response.status_code in range(300, 400) or any(header in response for header in NAVIGATING)
        if not request.htmx or request.htmx.boosted or navigating or LIVE_REFRESH in request.headers:
            return response
        stored = messages.get_messages(request)
        if getattr(stored, "used", False):  # a whole page, which shows them itself
            return response
        if shown := toasts(stored):
            response["X-Messages"] = json.dumps(shown)
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
