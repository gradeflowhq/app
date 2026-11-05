"""The runner protocol, and the runs it starts."""

import secrets
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, Self

type Task = Literal["mark", "test", "compare", "choose", "trial"]
"""What the sandbox runs: submissions marked, a method's test answers marked, texts compared by meaning, a model
endpoint's models chosen, or some of them tried."""
RUN_ID_PREFIX = "gf_"


@dataclass(frozen=True)
class Run:
    """A sandbox run of `kind` for `owner` on `key`, such as a remark of a marking on its revision."""

    kind: str
    owner: str
    key: str = ""
    nonce: str = field(default_factory=lambda: secrets.token_hex(4), compare=False)
    """Tells apart runs of the same key, such as a preview and its retry."""

    @property
    def id(self) -> str:
        """Its parts joined by _, so it fits in a path."""
        return f"{id_prefix(self.kind, self.owner)}{self.key}_{self.nonce}"

    @classmethod
    def parse(cls, run_id: str) -> Self | None:
        match run_id.removeprefix(RUN_ID_PREFIX).split("_"):
            case [kind, owner, key, nonce] if run_id.startswith(RUN_ID_PREFIX):
                return cls(kind, owner, key, nonce)
            case _:
                return None


def id_prefix(kind: str, owner: object = "") -> str:
    """The start of the IDs of `kind`'s runs, or of those for `owner`."""
    return f"{RUN_ID_PREFIX}{kind}_{owner}_" if owner != "" else f"{RUN_ID_PREFIX}{kind}_"


class State(StrEnum):
    QUEUED = "queued"
    """Waiting for a place or for its image."""
    RUNNING = "running"
    ENDED = "ended"


class Ending(StrEnum):
    EXITED = "exited"
    """By itself."""
    TIMED_OUT = "timed out"
    OUT_OF_MEMORY = "out of memory"
    FAILED = "failed"
    """A crash, a stop or a lost machine."""


class RunnerUnavailable(Exception):
    """The runner can't be reached, or refused a request."""


@dataclass(frozen=True)
class Launch:
    """What a run starts with."""

    task: Task
    priority: int
    """From 1 to 100; the higher, the sooner."""
    time_limit: int
    """Seconds it may run."""
    job_url: str
    """Where it fetches its input and calls back."""
    secret: str
    """What it signs its requests with."""
    model_endpoint: str | None = None
    """The model endpoint it asks language models at, with its token, as JSON."""

    def environment(self) -> dict[str, str]:
        """The sandbox script's environment."""
        environment = {"GRADEFLOW_JOB_URL": self.job_url, "GRADEFLOW_JOB_SECRET": self.secret}
        if self.model_endpoint is not None:
            environment["GRADEFLOW_MODEL_ENDPOINT"] = self.model_endpoint
        return environment


class Listener(Protocol):
    def changed(self, run: Run) -> None:
        """`run` was queued, started running or was forgotten."""

    def ended(self, run: Run, ending: Ending) -> None:
        """`run` ended; `ending` says how."""


class Runner(Protocol):
    """Each method raises `RunnerUnavailable` while the runner can't be reached."""

    def start(self, run: Run, launch: Launch) -> None: ...

    def stop(self, kind: str, owner: object) -> None:
        """Stop and forget `owner`'s runs of `kind`."""

    def forget(self, run: Run) -> None:
        """Stop and forget `run`, whose outcome is in."""

    def active(self, kind: str, owner: object = "") -> list[tuple[Run, State]]:
        """The runs of `kind`, or `owner`'s, that haven't ended."""

    def follow(self, listener: Listener) -> None:
        """Tell `listener` of each change to the runs until the runner can't be reached. It may tell a change twice."""
