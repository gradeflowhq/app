import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from gradeflow_app.jobs.nomad.client import Ending as NomadEnding
from gradeflow_app.jobs.nomad.client import Job, JobState, NomadUnavailable
from gradeflow_app.jobs.nomad.runner import NomadRunner
from gradeflow_app.jobs.runner import Ending, Launch, Run, RunnerUnavailable, State

LAUNCH = Launch(task="mark", priority=50, time_limit=60, job_url="http://app/jobs/x/", secret="s")


@dataclass
class FakeClient:
    """Nomad's API, as the jobs it holds and the lists a watch gives."""

    held: dict[str, JobState] = field(default_factory=dict)
    variables: dict[str, dict[str, str]] = field(default_factory=dict)
    specs: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    endings: dict[str, NomadEnding] = field(default_factory=dict)
    watched: list[list[Job]] = field(default_factory=list)
    is_down: bool = False

    def register(self, spec: dict[str, Any]) -> None:
        self._check_up()
        self.specs.append(spec)
        self.held[spec["ID"]] = JobState.QUEUED
        self.calls.append(f"register {spec['ID']}")

    def put_variable(self, job_id: str, items: dict[str, str]) -> None:
        self._check_up()
        self.variables[job_id] = items
        self.calls.append(f"put {job_id}")

    def stop(self, job_id: str) -> None:
        self._check_up()
        self.stopped.append(job_id)
        self.held.pop(job_id, None)
        self.variables.pop(job_id, None)

    def variable_ids(self, prefix: str) -> list[str]:
        self._check_up()
        self.calls.append("list variables")
        return [job_id for job_id in self.variables if job_id.startswith(prefix)]

    def delete_variable(self, job_id: str) -> None:
        self._check_up()
        self.variables.pop(job_id, None)

    def jobs(self, prefix: str) -> list[Job]:
        self._check_up()
        self.calls.append("list jobs")
        return [Job(job_id, state) for job_id, state in self.held.items() if job_id.startswith(prefix)]

    def watch(self, prefix: str) -> Iterator[list[Job]]:
        yield from self.watched
        raise NomadUnavailable

    def ending(self, job_id: str) -> NomadEnding:
        self._check_up()
        return self.endings[job_id]

    def _check_up(self) -> None:
        if self.is_down:
            raise NomadUnavailable


@dataclass
class Told:
    """A listener keeping what it was told."""

    changes: list[str] = field(default_factory=list)
    endings: list[tuple[str, Ending]] = field(default_factory=list)

    def changed(self, run: Run) -> None:
        self.changes.append(run.id)

    def ended(self, run: Run, ending: Ending) -> None:
        self.endings.append((run.id, ending))


def runner_on(client: FakeClient) -> NomadRunner:
    return NomadRunner(client, image="registry/sandbox:1", datacenter="dc1", namespace="gradeflow")  # type: ignore[arg-type]


def test_a_run_is_a_nomad_batch_job_of_the_sandbox_image() -> None:
    client = FakeClient()
    run = Run("remark", "m1", "3")
    runner_on(client).start(run, LAUNCH)
    [spec] = client.specs
    group = spec["TaskGroups"][0]
    task = group["Tasks"][0]
    assert (spec["ID"], spec["Namespace"], spec["Datacenters"], spec["Priority"]) == (run.id, "gradeflow", ["dc1"], 50)
    assert group["MaxRunDuration"] == 60 * 10**9
    assert group["RestartPolicy"]["Attempts"] == 1, "a crash is tried once more"
    assert [task["Config"]["image"], task["Config"]["command"], *task["Config"]["args"]] == [
        "registry/sandbox:1",
        "python",
        "/app/sandbox.py",
        "mark",
    ]
    assert task["Config"]["security_opt"] == ["no-new-privileges"]


def test_a_run_id_fits_in_a_nomad_variable_path() -> None:
    run = Run("version-tests", "123", "0123456789abcdef")
    assert re.fullmatch(r"[\w~-]+", run.id, re.ASCII)
    assert Run.parse(run.id) == run


def test_a_runs_environment_is_only_in_its_jobs_variable() -> None:
    client = FakeClient()
    run = Run("remark", "m1", "3")
    runner_on(client).start(run, LAUNCH)
    [spec] = client.specs
    [task] = spec["TaskGroups"][0]["Tasks"]
    [template] = task["Templates"]
    assert "Env" not in task
    assert client.variables == {run.id: LAUNCH.environment()}
    assert client.calls == [f"register {run.id}", f"put {run.id}"], "the variable after the job"
    assert template["Envvars"] is True
    assert template["DestPath"].startswith("secrets/")
    assert 'nomadVar (print "nomad/jobs/" (env "NOMAD_JOB_ID"))' in template["EmbeddedTmpl"]


def test_runs_are_listed_and_stopped_by_kind_and_owner() -> None:
    client = FakeClient()
    runner = runner_on(client)
    first, second, other = Run("preview", "d1", "a"), Run("preview", "d1", "b"), Run("preview", "d2", "a")
    for run in (first, second, other):
        runner.start(run, LAUNCH)
    client.held[second.id] = JobState.RUNNING
    client.held[other.id] = JobState.ENDED
    client.held["gradeflow-registry"] = JobState.RUNNING
    assert runner.active("preview") == [(first, State.QUEUED), (second, State.RUNNING)]
    runner.stop("preview", "d1")
    assert client.stopped == [first.id, second.id]
    runner.forget(other)
    assert client.held == {"gradeflow-registry": JobState.RUNNING}
    assert client.variables == {}


def test_following_tells_each_change_once_and_how_a_run_ended() -> None:
    queued, ended = Run("remark", "m1", "1"), Run("remark", "m2", "1")
    client = FakeClient(
        endings={ended.id: NomadEnding.OUT_OF_MEMORY},
        watched=[
            [Job(queued.id, JobState.QUEUED), Job("gradeflow-registry", JobState.RUNNING)],
            [Job(queued.id, JobState.QUEUED)],
            [Job(queued.id, JobState.RUNNING), Job(ended.id, JobState.ENDED)],
            [],
        ],
    )
    told = Told()
    with pytest.raises(RunnerUnavailable):
        runner_on(client).follow(told)
    assert told.changes == [queued.id, queued.id, queued.id, ended.id], "queued, running, then gone"
    assert told.endings == [(ended.id, Ending.OUT_OF_MEMORY)]


def test_following_deletes_the_variables_of_runs_that_ended_or_are_gone() -> None:
    queued, ended, gone = Run("remark", "m1", "1"), Run("remark", "m2", "1"), Run("remark", "m3", "1")
    client = FakeClient(endings={ended.id: NomadEnding.EXITED}, watched=[[Job(queued.id, JobState.QUEUED)]])
    client.held = {queued.id: JobState.QUEUED, ended.id: JobState.ENDED}
    client.variables = {job_id: {} for job_id in (queued.id, ended.id, gone.id, "other")}
    with pytest.raises(RunnerUnavailable):
        runner_on(client).follow(Told())
    assert client.variables.keys() == {queued.id, "other"}
    assert client.calls.index("list variables") < client.calls.index("list jobs"), "variables before jobs"


def test_nomad_out_of_reach_makes_the_runner_unavailable() -> None:
    client = FakeClient(is_down=True)
    runner = runner_on(client)
    with pytest.raises(RunnerUnavailable):
        runner.start(Run("remark", "m1", "1"), LAUNCH)
    with pytest.raises(RunnerUnavailable):
        runner.active("remark")


def test_a_run_whose_ending_cant_be_read_is_told_once_nomad_is_back() -> None:
    ended = Run("remark", "m1", "1")
    client = FakeClient(endings={ended.id: NomadEnding.TIMED_OUT}, watched=[[Job(ended.id, JobState.ENDED)]])
    told = Told()
    client.is_down = True
    with pytest.raises(RunnerUnavailable):
        runner_on(client).follow(told)
    assert told.endings == []
    client.is_down = False
    with pytest.raises(RunnerUnavailable):
        runner_on(client).follow(told)
    assert told.endings == [(ended.id, Ending.TIMED_OUT)], "a new follow starts from what Nomad holds"
