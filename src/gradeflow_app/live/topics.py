"""Topics that pages listen to, so they refresh what they show once a change to it is saved."""

import hashlib
from datetime import UTC, datetime
from weakref import WeakKeyDictionary

from django.core import signing
from django.db import connection, transaction
from django.http import HttpRequest
from django.urls import reverse
from django.utils import timezone

from gradeflow_app.live.models import Topic

CHANNEL = "gradeflow_live"
SPACING = "1s"
"""How often a page refreshes for one topic at most. It still refreshes once more for the last change."""
_SALT = "gradeflow.live"

_listened: WeakKeyDictionary[HttpRequest, set[str]] = WeakKeyDictionary()


def topic(*about: object) -> str:
    """The event name for what `about` names, like `("marks", question.pk)`; a hash makes it short and valid."""
    return "live-" + hashlib.blake2b(" ".join(map(str, about)).encode(), digest_size=8).hexdigest()


def publish(*names: str) -> None:
    """Have the pages listening to `names` refresh, and those that connect again later catch up."""
    now = timezone.now()
    with transaction.atomic():
        Topic.objects.bulk_create(
            [Topic(name=name, changed=now) for name in names],
            update_conflicts=True,
            unique_fields=["name"],
            update_fields=["changed"],
        )
        with connection.cursor() as cursor:
            for name in names:
                cursor.execute("SELECT pg_notify(%s, %s)", [CHANNEL, f"{name} {stamp(now)}"])


def listen(request: HttpRequest, *names: str) -> str:
    """The `hx-trigger` for `names`, which the page's stream sends."""
    _listened.setdefault(request, set()).update(names)
    return ", ".join(f"{name} from:body throttle:{SPACING}" for name in names)


def stream_url(request: HttpRequest) -> str | None:
    """Where the page streams the events it listens for; None if it listens for none."""
    if not (names := _listened.get(request)):
        return None
    token = signing.dumps([sorted(names), stamp(timezone.now())], salt=_SALT, compress=True)
    return f"{reverse('live')}?topics={token}"


def listened(token: str) -> tuple[frozenset[str], int]:
    """The topics of a stream URL's token, and when its page was rendered. Raises `signing.BadSignature`.

    Tokens don't expire: a page left open reconnects with its own, and a stream tells only when a topic changed."""
    names, rendered = signing.loads(token, salt=_SALT)
    return frozenset(names), rendered


def stamp(at: datetime) -> int:
    """Milliseconds since the epoch, which events carry as their IDs."""
    return int(at.timestamp() * 1000)


def unstamp(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, UTC)
