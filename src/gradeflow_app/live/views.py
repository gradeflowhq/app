import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from weakref import WeakKeyDictionary

import psycopg
from asgiref.sync import sync_to_async
from django.core import signing
from django.db import connections
from django.http import HttpRequest, HttpResponseBadRequest, StreamingHttpResponse
from django.http.response import HttpResponseBase

from gradeflow_app.live.models import Topic
from gradeflow_app.live.topics import CHANNEL, listened, stamp, unstamp

PING = 20.0
"""Seconds between comments, which keep idle streams open through proxies."""
OVERLAP = 10_000
"""Milliseconds of changes sent again on connecting: those saved while the page was read, or saved out of order."""

_LOG = logging.getLogger(__name__)

type Heard = asyncio.Queue[tuple[str, int] | None]
"""A stream's changes, by topic and time; None once the listener stops."""


async def stream(request: HttpRequest) -> HttpResponseBase:
    """The events of the topics in `?topics=`: the changes saved since the page was rendered, or since the last event
    the browser got, and then each change once it is saved."""
    try:
        names, since = listened(request.GET.get("topics", ""))
    except signing.BadSignature:
        return HttpResponseBadRequest()
    if (last := request.headers.get("Last-Event-ID", "")).isdigit():
        since = int(last)
    response = StreamingHttpResponse(_events(names, since - OVERLAP), content_type="text/event-stream")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"  # for nginx, which would hold the events back
    return response


async def _events(names: frozenset[str], since: int) -> AsyncIterator[bytes]:
    async with _listener().subscribe(names) as heard:
        for name, at in await _changed(names, since):
            yield _event(name, stamp(at))
        while True:
            try:
                item = await asyncio.wait_for(heard.get(), PING)
            except TimeoutError:
                yield b":\n\n"
                continue
            if item is None:
                return  # the browser connects again and catches up
            yield _event(*item)


@sync_to_async
def _changed(names: frozenset[str], since: int) -> list[tuple[str, datetime]]:
    """The topics among `names` changed after `since`, oldest first. Closes the connection, which the stream's thread
    would otherwise hold for as long as the stream stays open."""
    try:
        changed = Topic.objects.filter(name__in=names, changed__gt=unstamp(since)).order_by("changed")
        return list(changed.values_list("name", "changed"))
    finally:
        connections.close_all()


def _event(name: str, at: int) -> bytes:
    return f"id: {at}\nevent: {name}\n\n".encode()


class _Listener:
    """A connection listening on `CHANNEL` for each event loop, telling its streams of their topics' changes."""

    def __init__(self) -> None:
        self._streams: dict[str, set[Heard]] = {}
        self._task: asyncio.Task[None] | None = None
        self._listening = asyncio.Event()

    @asynccontextmanager
    async def subscribe(self, names: frozenset[str]) -> AsyncIterator[Heard]:
        """Yields once the listener is connected, so no change saved after the stream catches up is missed."""
        heard: Heard = asyncio.Queue()
        for name in names:
            self._streams.setdefault(name, set()).add(heard)
        try:
            if self._task is None or self._task.done():
                self._listening = asyncio.Event()
                self._task = asyncio.create_task(self._listen())
            await self._listening.wait()
            yield heard
        finally:
            for name in names:
                streams = self._streams[name]
                streams.discard(heard)
                if not streams:
                    del self._streams[name]

    async def _listen(self) -> None:
        try:
            params = connections["default"].get_connection_params()
            for synchronous in ("context", "cursor_factory"):
                params.pop(synchronous, None)
            # Keepalives notice a connection that died without closing, which would stop the changes silently.
            params |= {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}
            async with await psycopg.AsyncConnection.connect(**params, autocommit=True) as connection:
                await connection.execute(f"LISTEN {CHANNEL}")
                self._listening.set()
                async for notice in connection.notifies():
                    name, at = notice.payload.split()
                    for heard in self._streams.get(name, ()):
                        heard.put_nowait((name, int(at)))
        except Exception:
            _LOG.exception("Listening for changes failed")
        finally:
            self._listening.set()
            for streams in self._streams.values():
                for heard in streams:
                    heard.put_nowait(None)


_listeners: WeakKeyDictionary[asyncio.AbstractEventLoop, _Listener] = WeakKeyDictionary()


def _listener() -> _Listener:
    loop = asyncio.get_running_loop()
    if (listener := _listeners.get(loop)) is None:
        listener = _listeners[loop] = _Listener()
    return listener
