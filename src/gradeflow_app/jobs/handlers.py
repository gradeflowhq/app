"""What each kind of run does around its task in the sandbox: what it runs on, what its outcome changes, whether it
still applies, and who hears of it. The feature that starts a kind of run registers its handler."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from gradeflow_app.jobs.runner import Run, Task


def _lock_nothing(run: Run) -> None:
    pass


@dataclass(frozen=True)
class Handler:
    task: Task
    topics: Callable[[Run], list[str]]
    """What pages showing the run listen to."""
    input: Callable[[Run], BaseModel | None]
    """The run's input, built from its owner as it is now; None once the owner changed since the run started."""
    is_current: Callable[[Run], bool]
    """Whether its outcome still applies."""
    finish: Callable[[Run, Any], None]
    """Stores its outcome. Raises `ValueError` if the outcome doesn't fit the input."""
    fail: Callable[[Run, str], None]
    """Stores why it failed."""
    lock: Callable[[Run], None] = _lock_nothing
    """Locks what its outcome changes."""
    apply_event: Callable[[Run, dict[str, Any]], None] | None = None
    """Applies its task's own events as they arrive, such as a model test starting or ending."""
    priority: int = 70


_HANDLERS: dict[str, Handler] = {}


def register(kind: str, handler: Handler) -> None:
    _HANDLERS[kind] = handler


def handler(run: Run) -> Handler:
    return _HANDLERS[run.kind]


def is_registered(run: Run) -> bool:
    return run.kind in _HANDLERS


def parse_run(run_id: str) -> Run | None:
    """The run with `run_id`, if its kind is registered."""
    run = Run.parse(run_id)
    return run if run is not None and is_registered(run) else None
