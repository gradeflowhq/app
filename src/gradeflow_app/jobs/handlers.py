"""What each kind of job does beyond its task in the sandbox: what its outcome changes, whether it still applies, and
who hears of it. The feature that starts a kind of job registers its handler."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from gradeflow_app.jobs.models import Job

type Task = Literal["mark", "test", "compare", "choose", "trial"]
"""What the sandbox runs: a payload's submissions marked, a method's test answers marked, texts compared by meaning,
a model endpoint's models chosen, or some of them tried."""


def _lock_nothing(job: Job) -> None:
    pass


def _always_current(job: Job) -> bool:
    return True


@dataclass(frozen=True)
class Handler:
    task: Task
    topics: Callable[[Job], list[str]]
    """What pages showing the job listen to."""
    lock: Callable[[Job], None] = _lock_nothing
    """Locks what its outcome changes, before the job itself is locked."""
    is_current: Callable[[Job], bool] = _always_current
    """Whether its outcome still applies; one that doesn't fails as out of date."""
    apply_outcome: Callable[[Job, Any], None] | None = None
    """Applies its outcome: the marked submissions of a mark task, the comparison of a compare task. Without it, the
    job keeps the outcome as its result."""


_HANDLERS: dict[str, Handler] = {}


def register(kind: Job.Kind, handler: Handler) -> None:
    _HANDLERS[kind] = handler


def handler(job: Job) -> Handler:
    return _HANDLERS[job.kind]
