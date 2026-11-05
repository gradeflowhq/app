"""The runner on Nomad: each run is a batch job with the run's ID."""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from gradeflow_app.jobs.nomad.client import JOB_VARIABLE_PATH, Job, JobState, NomadClient, NomadUnavailable
from gradeflow_app.jobs.runner import RUN_ID_PREFIX, Ending, Launch, Listener, Run, RunnerUnavailable, State, id_prefix

ENVIRONMENT_TEMPLATE = (
    f'{{{{ with nomadVar (print "{JOB_VARIABLE_PATH}" (env "NOMAD_JOB_ID")) }}}}'
    "{{ range .Tuples }}{{ .K }}={{ .V | toJSON }}\n{{ end }}{{ end }}"
)
"""The task's environment, from the job's variable."""


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
                self._delete_leftover_variables()

    def _delete_leftover_variables(self) -> None:
        """Delete the variables of jobs that ended or are gone, left by a failed `forget`."""
        # Listed before the jobs, since a variable is put only after its job is registered.
        job_ids = self.client.variable_ids(RUN_ID_PREFIX)
        active = {job.id for job in self.client.jobs(RUN_ID_PREFIX) if job.state is not JobState.ENDED}
        for job_id in set(job_ids) - active:
            self.client.delete_variable(job_id)

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
