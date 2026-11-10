"""Starting, stopping and ending runs. A run fetches its input from the app and calls back with its outcome, which its
owner stores."""

import json
import logging
from contextlib import suppress
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, TypedDict, Unpack

from django.conf import settings
from django.core.cache import cache as django_cache
from django.db import transaction
from django.urls import reverse
from django.utils.crypto import salted_hmac
from gradeflow_core import ModelEndpoint
from pydantic import ValidationError

from gradeflow_app.jobs.handlers import handler, is_registered
from gradeflow_app.jobs.nomad.client import NomadClient
from gradeflow_app.jobs.nomad.runner import NomadRunner
from gradeflow_app.jobs.runner import Ending, Launch, Run, Runner, RunnerUnavailable, State
from gradeflow_app.live.topics import notify_change

UNAVAILABLE_REASON = "The marking service is unavailable."
UNREADABLE_REASON = "The marking service sent results that can't be read."
ENDING_REASONS = {
    Ending.TIMED_OUT: "Ran past its time limit.",
    Ending.OUT_OF_MEMORY: "Ran out of memory.",
    Ending.FAILED: "The marking service stopped before finishing.",
}
"""Why a run that ended without calling back failed, by how it ended."""
ENDED_LIFETIME = 24 * 60 * 60
"""Seconds an ended run's late callbacks are ignored for."""
_LOG = logging.getLogger(__name__)


class Progress(TypedDict, total=False):
    progress: float
    progress_text: str


@dataclass(frozen=True)
class RunState:
    """How a run is going, or how the last one went, as pages show it."""

    status: Literal["queued", "running", "completed", "failed"]
    error: str = ""
    progress: float = 0
    progress_text: str = ""

    @property
    def is_active(self) -> bool:
        return self.status in ("queued", "running")


COMPLETED = RunState("completed")


@cache
def runner() -> Runner:
    config = settings.GRADEFLOW_NOMAD
    client = NomadClient(config["url"], config["token"], config["namespace"])
    return NomadRunner(client, image=config["image"], datacenter=config["datacenter"], namespace=config["namespace"])


def secret(run: Run) -> str:
    """What `run` signs its requests with."""
    return salted_hmac("gradeflow.jobs", run.id, algorithm="sha256").hexdigest()


def start(run: Run, model_endpoint: ModelEndpoint | None = None) -> None:
    """Start `run` once the transaction commits, stopping its owner's other runs. It asks language models at
    `model_endpoint`."""

    def submit() -> None:
        try:
            runner().stop(run.kind, run.owner)
            runner().start(run, _launch(run, model_endpoint))
        except RunnerUnavailable:
            _LOG.warning("Couldn't start %s", run.id, exc_info=True)
            end(run, error=UNAVAILABLE_REASON)

    transaction.on_commit(submit)


def stop(kind: str, owner: object) -> None:
    """Stop `owner`'s runs of `kind` once the transaction commits."""

    def stop_runs() -> None:
        with suppress(RunnerUnavailable):
            runner().stop(kind, owner)

    transaction.on_commit(stop_runs)


def active(kind: str, owner: object = "") -> list[tuple[Run, RunState]]:
    """The runs of `kind`, or `owner`'s, that haven't ended, with their progress; none while the runner can't be
    reached."""
    try:
        listed = runner().active(kind, owner)
    except RunnerUnavailable:
        _LOG.warning("Couldn't list the runs of %s", kind, exc_info=True)
        return []
    return [(run, _running_state(run, state)) for run, state in listed]


def end(run: Run, *, outcome: Any = None, error: str | None = None) -> None:
    """Store what `run` gave, or why it failed, unless it no longer applies or has ended already; then forget it."""
    run_handler = handler(run)
    with transaction.atomic():
        run_handler.lock(run)
        if not django_cache.add(_ended_key(run), True, timeout=ENDED_LIFETIME):
            return  # a late or repeated callback
        if run_handler.is_current(run):
            if error is None:
                try:
                    with transaction.atomic():
                        run_handler.finish(run, outcome)
                except ValidationError:
                    error = UNREADABLE_REASON
                except ValueError as invalid:
                    error = str(invalid)
            if error is not None:
                _LOG.warning("%s failed: %s", run.id, error)
                run_handler.fail(run, error)
    django_cache.delete(_progress_key(run))
    _forget(run)
    notify(run)


def apply_event(run: Run, event: dict[str, Any]) -> None:
    """An event of the run's own task, applied while the run still applies."""
    run_handler = handler(run)
    if run_handler.apply_event is None:
        raise ValueError("Unknown job event.")
    with transaction.atomic():
        run_handler.lock(run)
        if run_handler.is_current(run):
            run_handler.apply_event(run, event)
    notify(run)


def record_progress(run: Run, **progress: Unpack[Progress]) -> None:
    """Keep how far the run has got, such as its `progress` or what it waits on as `progress_text`."""
    key = _progress_key(run)
    django_cache.set(key, django_cache.get(key, {}) | progress, timeout=settings.GRADEFLOW_MODEL_JOB_TIME_LIMIT)
    notify(run)


def notify(run: Run) -> None:
    if topics := handler(run).topics(run):
        notify_change(*topics)


class _Listener:
    """Pages hear of each change, and a run that ended without calling back fails with the runner's reason."""

    def changed(self, run: Run) -> None:
        if is_registered(run):
            notify(run)

    def ended(self, run: Run, ending: Ending) -> None:
        if not is_registered(run):
            return
        if ending is Ending.EXITED:
            _forget(run)
        else:
            end(run, error=ENDING_REASONS[ending])


listener = _Listener()


def _forget(run: Run) -> None:
    with suppress(RunnerUnavailable):  # the runner's own clean-up forgets it later
        runner().forget(run)


def _launch(run: Run, model_endpoint: ModelEndpoint | None) -> Launch:
    run_handler = handler(run)
    job_url = settings.GRADEFLOW_APP_URL_FOR_JOBS.rstrip("/") + reverse("job_input", args=[run.id]).removesuffix(
        "input"
    )
    endpoint_json = None
    if model_endpoint is not None:
        endpoint_json = json.dumps(
            model_endpoint.model_dump(mode="json") | {"api_key": model_endpoint.api_key.get_secret_value()}
        )
    return Launch(
        task=run_handler.task,
        priority=run_handler.priority,
        time_limit=settings.GRADEFLOW_MODEL_JOB_TIME_LIMIT if model_endpoint else settings.GRADEFLOW_JOB_TIME_LIMIT,
        job_url=job_url,
        secret=secret(run),
        model_endpoint=endpoint_json,
    )


def _running_state(run: Run, state: State) -> RunState:
    progress: Progress = django_cache.get(_progress_key(run), {})
    return RunState("running" if state is State.RUNNING else "queued", **progress)


def _progress_key(run: Run) -> str:
    return f"run-progress:{run.id}"


def _ended_key(run: Run) -> str:
    return f"run-ended:{run.id}"
