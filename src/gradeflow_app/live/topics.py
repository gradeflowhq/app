"""Topics that pages listen to, so they refresh what they show once a change to it is saved."""

import hashlib
from datetime import UTC, datetime

from django.core import signing
from django.db import connection, transaction
from django.urls import reverse
from django.utils import timezone

from gradeflow_app.live.models import Topic
from gradeflow_app.requests import HtmxRequest

NOTIFY_CHANNEL = "gradeflow_live"
REFRESH_THROTTLE = "1s"
"""How often a page refreshes for one topic at most. It still refreshes once more for the last change."""
_SALT = "gradeflow.live"


def topic(*about: object) -> str:
    """The event name for what `about` names, like `("marks", question.pk)`; a hash makes it short and valid."""
    return "live-" + hashlib.blake2b(" ".join(map(str, about)).encode(), digest_size=8).hexdigest()


def notify_change(*names: str) -> None:
    """Have the pages listening to `names` refresh, and those that connect again later catch up."""
    now = timezone.now()
    with transaction.atomic():
        Topic.objects.bulk_create(
            [Topic(name=name, changed_at=now) for name in names],
            update_conflicts=True,
            unique_fields=["name"],
            update_fields=["changed_at"],
        )
        with connection.cursor() as cursor:
            for name in names:
                cursor.execute("SELECT pg_notify(%s, %s)", [NOTIFY_CHANNEL, f"{name} {to_milliseconds(now)}"])


def listen(request: HtmxRequest, *names: str) -> str:
    """The `hx-trigger` for `names`, which the page's stream sends."""
    request.live_topics = getattr(request, "live_topics", set()) | set(names)
    return ", ".join(f"{name} from:body throttle:{REFRESH_THROTTLE}" for name in names)


def stream_url(request: HtmxRequest) -> str | None:
    """Where the page streams the events it listens for; None if it listens for none."""
    if not (names := getattr(request, "live_topics", None)):
        return None
    token = signing.dumps([sorted(names), to_milliseconds(timezone.now())], salt=_SALT, compress=True)
    return reverse("live", query={"topics": token})


def read_stream_token(token: str) -> tuple[frozenset[str], int]:
    """The topics of a stream URL's token, and when its page was rendered. Raises `signing.BadSignature`.

    Tokens don't expire: a page left open reconnects with its own, and a stream tells only when a topic changed."""
    names, rendered_at = signing.loads(token, salt=_SALT)
    return frozenset(names), rendered_at


def to_milliseconds(at: datetime) -> int:
    """Milliseconds since the epoch, which events carry as their IDs."""
    return int(at.timestamp() * 1000)


def from_milliseconds(milliseconds: int) -> datetime:
    return datetime.fromtimestamp(milliseconds / 1000, UTC)
