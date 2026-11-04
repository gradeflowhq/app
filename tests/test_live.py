"""Pages listen to the topics of what they show, and refresh once a change to it is saved."""

import asyncio
import re
from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import cast

import pytest
from asgiref.sync import sync_to_async
from django.db import connections
from django.http import StreamingHttpResponse
from django.test import AsyncClient, Client, RequestFactory
from django.utils import timezone

from gradeflow_app.accounts.models import User
from gradeflow_app.live import topics
from gradeflow_app.live.models import Topic
from gradeflow_app.live.views import _changed
from gradeflow_app.requests import HtmxRequest

MARKS = topics.topic("marks", "Q1")
JOBS = topics.topic("jobs", "Q1")


def stream_url(name: str) -> str:
    request = cast(HtmxRequest, RequestFactory().get("/"))
    topics.listen(request, name)
    url = topics.stream_url(request)
    assert url is not None
    return url


async def events(user: User, url: str, headers: dict[str, str] | None = None) -> AsyncGenerator[bytes]:
    client = AsyncClient()
    await client.aforce_login(user)
    response = await client.get(url, headers=headers)
    assert (response.status_code, response["Content-Type"]) == (200, "text/event-stream")
    async for chunk in cast(StreamingHttpResponse, response):
        yield chunk


def event(name: str) -> re.Pattern[bytes]:
    return re.compile(rf"id: \d+\nevent: {name}\n\n".encode())


@pytest.mark.django_db(transaction=True)
def test_a_stream_catches_up_on_changes_saved_as_its_page_was_read_then_sends_new_ones(user: User) -> None:
    topics.notify_change(MARKS)
    url = stream_url(MARKS)

    async def read() -> list[bytes]:
        stream = events(user, url)
        earlier = await anext(stream)
        await sync_to_async(topics.notify_change)(JOBS)  # not listened for
        await sync_to_async(topics.notify_change)(MARKS)
        new = await anext(stream)
        await stream.aclose()
        await sync_to_async(connections.close_all)()
        return [earlier, new]

    assert [bool(event(MARKS).fullmatch(chunk)) for chunk in asyncio.run(read())] == [True, True]


@pytest.mark.django_db(transaction=True)
def test_a_stream_connecting_again_skips_what_its_browser_got(user: User, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gradeflow_app.live.views.KEEPALIVE_SECONDS", 0.01)
    url = stream_url(MARKS)
    Topic.objects.create(name=MARKS, changed_at=timezone.now() - timedelta(minutes=1))

    async def first_chunk(headers: dict[str, str]) -> bytes:
        stream = events(user, url, headers)
        chunk = await anext(stream)
        await stream.aclose()
        await sync_to_async(connections.close_all)()
        return chunk

    assert asyncio.run(first_chunk({"Last-Event-ID": str(topics.to_milliseconds(timezone.now()))})) == b":\n\n"


@pytest.mark.django_db(transaction=True)
def test_catching_up_frees_its_database_connection_for_the_rest_of_the_stream() -> None:
    async def connected() -> bool:
        await _changed(frozenset({MARKS}), 0)
        return await sync_to_async(lambda: connections["default"].connection is not None)()

    assert not asyncio.run(connected())


def test_a_stream_takes_only_the_topics_gradeflow_signed(client: Client) -> None:
    assert client.get("/live/?topics=live-0123456789abcdef").status_code == 400
