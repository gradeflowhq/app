import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pytest

from gradeflow_app.jobs.nomad.client import Ended, Job, JobState, NomadUnavailable, Placement
from gradeflow_app.jobs.nomad.client import Ending as NomadEnding
from gradeflow_app.jobs.nomad.runner import NomadRunner
from gradeflow_app.jobs.runner import Ending, Launch, Run, RunnerUnavailable, State

LAUNCH = Launch(task="mark", priority=50, time_limit=60, job_url="http://app/jobs/x/", secret="s")


@dataclass
class FakeClient:
    """Nomad's API, as the jobs it holds and the changes its event stream gives."""

    held: dict[str, JobState] = field(default_factory=dict)
    variables: dict[str, dict[str, str]] = field(default_factory=dict)
    specs: list[dict[str, Any]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    placements: dict[str, Placement] = field(default_factory=dict)
    endings: dict[str, NomadEnding] = field(default_factory=dict)
    streamed: list[Job | Ended] = field(default_factory=list)
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
        self.held.pop(job_id, None)
        self.variables.pop(job_id, None)

    def variable_ids(self, prefix: str) -> list[str]:
        self._check_up()
        self.calls.append("list variables")
        return [job_id for job_id in self.variables if job_id.startswith(prefix)]

    def delete_variable(self, job_id: str) -> None:
        self._check_up()
        self.variables.pop(job_id, None)

    def jobs(self, prefix: str) -> tuple[list[Job], int]:
        self._check_up()
        self.calls.append("list jobs")
        return [Job(job_id, state) for job_id, state in self.held.items() if job_id.startswith(prefix)], 41

    def placement(self, job_id: str) -> Placement | None:
        self._check_up()
        return self.placements.get(job_id)

    def ended(self, job_id: str) -> Ended:
        self._check_up()
        return Ended(job_id, self.endings[job_id], "The task stopped (exit code 1).", "Terminated: Exit Code")

    def changes(self, index: int) -> Iterator[Job | Ended]:
        assert index == 41, "from the listing's index"
        yield from self.streamed
        raise NomadUnavailable

    def _check_up(self) -> None:
        if self.is_down:
            raise NomadUnavailable


@dataclass
class Told:
    """A listener keeping what it was told."""

    changes: list[str] = field(default_factory=list)
    endings: list[tuple[str, Ending]] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    def changed(self, run: Run) -> None:
        self.changes.append(run.id)

    def ended(self, run: Run, ending: Ending, details: str) -> None:
        self.endings.append((run.id, ending))
        self.details.append(details)


def runner_on(client: FakeClient) -> NomadRunner:
    return NomadRunner(client, image="registry/sandbox:1", datacenter="dc1", namespace="gradeflow")  # type: ignore[arg-type]


def test_a_run_is_a_nomad_batch_job_of_the_sandbox_image_tried_once() -> None:
    client = FakeClient()
    run = Run("remark", "m1", "3")
    runner_on(client).start(run, LAUNCH)
    [spec] = client.specs
    group = spec["TaskGroups"][0]
    task = group["Tasks"][0]
    assert (spec["ID"], spec["Namespace"], spec["Datacenters"], spec["Priority"]) == (run.id, "gradeflow", ["dc1"], 50)
    assert group["MaxRunDuration"] == 60 * 10**9
    assert group["RestartPolicy"]["Attempts"] == group["ReschedulePolicy"]["Attempts"] == 0
    assert group["ReschedulePolicy"]["Unlimited"] is False
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


def test_active_runs_say_why_nomad_couldnt_place_them() -> None:
    queued, no_machine, no_room, running, ended = (Run("preview", "d1", key) for key in "abcde")
    client = FakeClient(placements={no_machine.id: Placement.NO_NODE, no_room.id: Placement.NO_ROOM})
    client.held = {
        queued.id: JobState.QUEUED,
        no_machine.id: JobState.UNPLACED,
        no_room.id: JobState.UNPLACED,
        running.id: JobState.RUNNING,
        ended.id: JobState.ENDED,
        Run("preview", "d2", "a").id: JobState.QUEUED,
        "gradeflow-registry": JobState.RUNNING,
    }
    assert runner_on(client).active("preview", "d1") == [
        (queued, State.QUEUED),
        (no_machine, State.NO_MACHINE),
        (no_room, State.NO_ROOM),
        (running, State.RUNNING),
    ]


def test_a_forgotten_run_is_purged_with_its_variable() -> None:
    client = FakeClient()
    runner = runner_on(client)
    run = Run("remark", "m1", "1")
    runner.start(run, LAUNCH)
    runner.forget(run)
    assert (client.held, client.variables) == ({}, {})


def test_following_tells_what_nomad_holds_then_each_change(caplog: pytest.LogCaptureFixture) -> None:
    queued, ended, streamed = Run("remark", "m1", "1"), Run("remark", "m2", "1"), Run("remark", "m3", "1")
    client = FakeClient(
        held={queued.id: JobState.QUEUED, ended.id: JobState.ENDED},
        endings={ended.id: NomadEnding.OUT_OF_MEMORY},
        streamed=[
            Job(streamed.id, JobState.RUNNING),
            Job("gradeflow-registry", JobState.RUNNING),
            Ended(
                streamed.id,
                NomadEnding.NOT_STARTED,
                "Nomad couldn't pull the image.",
                "Driver Failure: Failed to pull `registry/sandbox:1`",
            ),
        ],
    )
    told = Told()
    with caplog.at_level(logging.WARNING), pytest.raises(RunnerUnavailable):
        runner_on(client).follow(told)
    assert told.changes == [queued.id, streamed.id]
    assert told.endings == [(ended.id, Ending.OUT_OF_MEMORY), (streamed.id, Ending.NOT_STARTED)]
    assert told.details[1] == "Nomad couldn't pull the image.", "names no image"
    assert (
        f"Nomad job {streamed.id} ended (not started): Driver Failure: Failed to pull `registry/sandbox:1`"
        in caplog.text
    )


def test_following_first_deletes_the_variables_of_runs_that_ended_or_are_gone() -> None:
    queued, ended, gone = Run("remark", "m1", "1"), Run("remark", "m2", "1"), Run("remark", "m3", "1")
    client = FakeClient(
        held={queued.id: JobState.QUEUED, ended.id: JobState.ENDED}, endings={ended.id: NomadEnding.EXITED}
    )
    client.variables = {job_id: {} for job_id in (queued.id, ended.id, gone.id, "other")}
    with pytest.raises(RunnerUnavailable):
        runner_on(client).follow(Told())
    assert client.variables.keys() == {queued.id, "other"}
    assert client.calls.index("list variables") < client.calls.index("list jobs"), "variables before jobs"


def test_nomad_out_of_reach_makes_the_runner_unavailable() -> None:
    runner = runner_on(FakeClient(is_down=True))
    with pytest.raises(RunnerUnavailable):
        runner.start(Run("remark", "m1", "1"), LAUNCH)
    with pytest.raises(RunnerUnavailable):
        runner.active("remark")
    with pytest.raises(RunnerUnavailable):
        runner.follow(Told())
