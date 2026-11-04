"""The runner on Nomad: each run is a batch job with the run's ID."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from gradeflow_app.jobs.nomad.client import Job, JobState, NomadClient, NomadUnavailable
from gradeflow_app.jobs.runner import RUN_ID_PREFIX, Ending, Launch, Listener, Run, RunnerUnavailable, State, id_prefix


class NomadRunner:
    def __init__(self, client: NomadClient, *, image: str, datacenter: str, namespace: str) -> None:
        self.client = client
        self.image = image
        self.datacenter = datacenter
        self.namespace = namespace

    def start(self, run: Run, launch: Launch) -> None:
        with _runner_errors():
            self.client.register(self._spec(run, launch))

    def stop(self, kind: str, owner: object) -> None:
        with _runner_errors():
            for job in self.client.jobs(id_prefix(kind, owner)):
                self.client.stop(job.id)

    def forget(self, run: Run) -> None:
        with _runner_errors():
            self.client.stop(run.id)

    def active(self, kind: str, owner: object = "") -> list[tuple[Run, State]]:
        with _runner_errors():
            jobs = self.client.jobs(id_prefix(kind, owner))
        return [
            (run, State(job.state.value))
            for job in jobs
            if job.state is not JobState.ENDED and (run := Run.parse(job.id)) is not None
        ]

    def follow(self, listener: Listener) -> None:
        known: dict[str, JobState] = {}
        with _runner_errors():
            for jobs in self.client.watch(RUN_ID_PREFIX):
                known = self._tell(listener, known, jobs)

    def _tell(self, listener: Listener, known: dict[str, JobState], jobs: list[Job]) -> dict[str, JobState]:
        """Tell `listener` what changed in `jobs` since `known`, and return their states now."""
        current = {job.id: job.state for job in jobs}
        changed = {job_id for job_id, state in current.items() if known.get(job_id) != state} | (known.keys() - current)
        for job_id in sorted(changed):
            if (run := Run.parse(job_id)) is None:
                continue
            if current.get(job_id) is JobState.ENDED:
                listener.ended(run, Ending(self.client.ending(job_id).value))
            else:
                listener.changed(run)
        return current

    def _spec(self, run: Run, launch: Launch) -> dict[str, Any]:
        """The Nomad job that runs `run`."""
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
                    "RestartPolicy": {
                        "Attempts": 1,
                        "Delay": 5_000_000_000,
                        "Interval": 86_400_000_000_000,
                        "Mode": "fail",
                    },
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
                            "Env": launch.environment(),
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
