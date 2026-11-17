"""The runner on Nomad: each run is a batch job with the run's ID, tried once."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from gradeflow_app.jobs.nomad.client import (
    JOB_VARIABLE_PATH,
    Ended,
    Job,
    JobState,
    NomadClient,
    NomadUnavailable,
    Placement,
)
from gradeflow_app.jobs.nomad.client import Ending as NomadEnding
from gradeflow_app.jobs.runner import RUN_ID_PREFIX, Ending, Launch, Listener, Run, RunnerUnavailable, State, id_prefix

_LOG = logging.getLogger(__name__)
ENVIRONMENT_TEMPLATE = (
    f'{{{{ with nomadVar (print "{JOB_VARIABLE_PATH}" (env "NOMAD_JOB_ID")) }}}}'
    "{{ range .Tuples }}{{ .K }}={{ .V | toJSON }}\n{{ end }}{{ end }}"
)
"""The task's environment, from the job's variable."""
_STATES = {JobState.QUEUED: State.QUEUED, JobState.UNPLACED: State.QUEUED, JobState.RUNNING: State.RUNNING}
_WAITING_STATES = {Placement.NO_NODE: State.NO_MACHINE, Placement.NO_ROOM: State.NO_ROOM}


class NomadRunner:
    def __init__(self, client: NomadClient, *, image: str, datacenter: str, namespace: str) -> None:
        self.client = client
        self.image = image
        self.datacenter = datacenter
        self.namespace = namespace

    def start(self, run: Run, launch: Launch) -> None:
        with _runner_errors():
            self.client.register(self._spec(run, launch))
            self.client.put_variable(run.id, launch.environment())

    def forget(self, run: Run) -> None:
        with _runner_errors():
            self.client.stop(run.id)

    def active(self, kind: str, owner: object = "") -> list[tuple[Run, State]]:
        with _runner_errors():
            jobs, _ = self.client.jobs(id_prefix(kind, owner))
            return [
                (run, self._state(job))
                for job in jobs
                if job.state is not JobState.ENDED and (run := Run.parse(job.id)) is not None
            ]

    def follow(self, listener: Listener) -> None:
        with _runner_errors():
            # Listed before the jobs, since a variable is put only after its job is registered.
            variable_ids = self.client.variable_ids(RUN_ID_PREFIX)
            jobs, index = self.client.jobs(RUN_ID_PREFIX)
            for job_id in set(variable_ids) - {job.id for job in jobs if job.state is not JobState.ENDED}:
                self.client.delete_variable(job_id)
            for job in jobs:
                self._tell(listener, self.client.ended(job.id) if job.state is JobState.ENDED else job)
            for change in self.client.changes(index):
                self._tell(listener, change)

    def _state(self, job: Job) -> State:
        if job.state is JobState.UNPLACED and (placement := self.client.placement(job.id)) is not None:
            return _WAITING_STATES[placement]
        return _STATES[job.state]

    def _tell(self, listener: Listener, change: Job | Ended) -> None:
        match change:
            case Ended(job_id, ending, details, message) if (run := Run.parse(job_id)) is not None:
                if ending is not NomadEnding.EXITED:
                    _LOG.warning("Nomad job %s ended (%s): %s", job_id, ending, message)
                listener.ended(run, Ending(ending.value), details)
            case Job(job_id) if (run := Run.parse(job_id)) is not None:
                listener.changed(run)

    def _spec(self, run: Run, launch: Launch) -> dict[str, Any]:
        """The Nomad job that runs `run` once."""
        return {
            "ID": run.id,
            "Name": run.id,
            "Namespace": self.namespace,
            "Type": "batch",
            "Datacenters": [self.datacenter],
            "Priority": launch.priority,
            "TaskGroups": [
                {
                    "Name": launch.task,
                    "MaxRunDuration": launch.time_limit * 1_000_000_000,
                    "RestartPolicy": {"Attempts": 0, "Mode": "fail"},
                    "ReschedulePolicy": {"Attempts": 0, "Unlimited": False},
                    "Tasks": [
                        {
                            "Name": launch.task,
                            "Driver": "docker",
                            "Config": {
                                "image": self.image,
                                "force_pull": False,
                                "command": "python",
                                "args": ["/app/sandbox.py", launch.task],
                                "security_opt": ["no-new-privileges"],
                            },
                            # Anyone who can read jobs can read a job's Env; only the job can read its variable.
                            "Templates": [
                                {"EmbeddedTmpl": ENVIRONMENT_TEMPLATE, "DestPath": "secrets/env", "Envvars": True}
                            ],
                            "Resources": {"CPU": 1000, "MemoryMB": 1024},
                        }
                    ],
                }
            ],
        }


@contextmanager
def _runner_errors() -> Iterator[None]:
    """Raises `RunnerUnavailable` for `NomadUnavailable`."""
    try:
        yield
    except NomadUnavailable as error:
        raise RunnerUnavailable from error
