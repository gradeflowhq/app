import json
from typing import Any

import httpx
import pytest

from gradeflow_app.jobs.nomad.client import (
    Ended,
    Ending,
    Job,
    JobState,
    NomadClient,
    NomadUnavailable,
    Placement,
)


def nomad_answering(respond: Any) -> NomadClient:
    nomad = NomadClient(url="http://nomad", token="secret", namespace="gradeflow")
    nomad.http = httpx.Client(
        base_url="http://nomad",
        headers=nomad.http.headers,
        params=nomad.http.params,
        transport=httpx.MockTransport(respond),
    )
    return nomad


def listed(job_id: str, status: str, *, running: int = 0, queued: int = 0) -> dict[str, Any]:
    summary = {"Queued": queued, "Starting": 0, "Running": running, "Complete": 0, "Failed": 0, "Lost": 0}
    return {"ID": job_id, "Status": status, "JobSummary": {"Summary": {"mark": summary}}}


def test_jobs_are_registered_stopped_and_listed_in_the_namespace() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            jobs = [
                listed("a_1", "pending"),
                listed("a_2", "pending", queued=1),
                listed("a_3", "running", running=1),
                listed("a_4", "dead"),
            ]
            return httpx.Response(200, json=jobs, headers={"X-Nomad-Index": "42"})
        return httpx.Response(200, json={})

    nomad = nomad_answering(respond)
    nomad.register({"ID": "a_1"})
    nomad.stop("a_1")
    assert nomad.jobs("a_") == (
        [
            Job("a_1", JobState.QUEUED),
            Job("a_2", JobState.UNPLACED),
            Job("a_3", JobState.RUNNING),
            Job("a_4", JobState.ENDED),
        ],
        42,
    )
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/jobs"),
        ("DELETE", "/v1/job/a_1"),
        ("DELETE", "/v1/var/nomad/jobs/a_1"),
        ("GET", "/v1/jobs"),
    ]
    assert all(request.url.params["namespace"] == "gradeflow" for request in requests)
    assert all(request.headers["X-Nomad-Token"] == "secret" for request in requests)
    assert requests[1].url.params["purge"] == "true"
    assert requests[3].url.params["prefix"] == "a_"


def test_a_jobs_variable_is_at_its_job_path() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json=[{"Path": "nomad/jobs/a_1"}, {"Path": "nomad/jobs/a_2"}])
        return httpx.Response(200, json={})

    nomad = nomad_answering(respond)
    nomad.put_variable("a_1", {"TOKEN": "t"})
    assert nomad.variable_ids("a_") == ["a_1", "a_2"]
    nomad.delete_variable("a_2")
    assert [(request.method, request.url.path) for request in requests] == [
        ("PUT", "/v1/var/nomad/jobs/a_1"),
        ("GET", "/v1/vars"),
        ("DELETE", "/v1/var/nomad/jobs/a_2"),
    ]
    assert json.loads(requests[0].content) == {"Items": {"TOKEN": "t"}}
    assert requests[1].url.params["prefix"] == "nomad/jobs/a_"


def evaluation(index: int, *exhausted: int) -> dict[str, Any]:
    """An evaluation that couldn't place a group on any node, or with `exhausted` nodes full, one count per group."""
    failed = {f"g{group}": {"NodesEvaluated": count, "NodesExhausted": count} for group, count in enumerate(exhausted)}
    return {"CreateIndex": index, "Status": "blocked", "FailedTGAllocs": failed or {"g": {"NodesExhausted": 0}}}


@pytest.mark.parametrize(
    ("evaluations", "placement"),
    [
        ([evaluation(1)], Placement.NO_NODE),
        ([evaluation(1, 0, 2)], Placement.NO_ROOM),
        ([evaluation(1, 2), evaluation(2)], Placement.NO_NODE),
        ([{"CreateIndex": 1, "Status": "complete", "FailedTGAllocs": None}], None),
    ],
)
def test_why_a_job_isnt_placed_is_its_latest_evaluation_that_failed_to(
    evaluations: list[dict[str, Any]], placement: Placement | None
) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/job/a_1/evaluations"
        return httpx.Response(200, json=evaluations)

    assert nomad_answering(respond).placement("a_1") == placement


def allocation(
    index: int = 1,
    client_status: str = "complete",
    *,
    description: str = "All tasks have completed",
    desired_status: str = "run",
    failed_before_start: tuple[str, str] | None = None,
    **details: str,
) -> dict[str, Any]:
    """An allocation whose task started and was terminated with `details`, or failed with an event of a type and
    message before it started."""
    events: list[dict[str, Any]] = [
        {"Type": "Started", "DisplayMessage": "Task started", "Details": {}},
        {"Type": "Terminated", "DisplayMessage": "Exit Code", "Details": details},
    ]
    if failed_before_start is not None:
        event_type, message = failed_before_start
        events = [
            {"Type": event_type, "DisplayMessage": message, "Details": {}, "FailsTask": True},
            {"Type": "Not Restarting", "DisplayMessage": "Policy allows no restarts", "Details": {}, "FailsTask": True},
        ]
    return {
        "ID": f"9e62cbbd-0000-{index:04}",
        "JobID": "a_1",
        "CreateIndex": index,
        "ClientStatus": client_status,
        "DesiredStatus": desired_status,
        "ClientDescription": description,
        "TaskStates": {"mark": {"Events": events}},
    }


UNPULLED = ("Driver Failure", "Failed to pull `localhost:5055/sandbox:1`: dial tcp [::1]:5055: i/o timeout")
UNSET_UP = ("Setup Failure", "failed to build task directory")


@pytest.mark.parametrize(
    ("allocations", "ending"),
    [
        ([allocation(exit_code="0")], Ending.EXITED),
        ([allocation(exit_code="1"), allocation(2, exit_code="0")], Ending.EXITED),
        ([allocation(description="allocation exceeded max_run_duration", exit_code="137")], Ending.TIMED_OUT),
        ([allocation(client_status="failed", exit_code="137", oom_killed="true")], Ending.OUT_OF_MEMORY),
        ([allocation(client_status="failed", exit_code="1")], Ending.FAILED),
        ([allocation(client_status="failed", failed_before_start=UNPULLED)], Ending.NOT_STARTED),
        ([allocation(client_status="failed", failed_before_start=UNSET_UP)], Ending.NOT_STARTED),
        ([allocation(client_status="lost", desired_status="stop")], Ending.LOST),
    ],
)
def test_how_a_job_ended_is_its_latest_allocations(allocations: list[dict[str, Any]], ending: Ending) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/job/a_1/allocations"
        return httpx.Response(200, json=allocations)

    assert nomad_answering(respond).ended("a_1").ending is ending


@pytest.mark.parametrize(
    ("allocation_ended", "details"),
    [
        (
            allocation(client_status="failed", description="Failed tasks", failed_before_start=UNPULLED),
            "Nomad couldn't pull the image.",
        ),
        (
            allocation(client_status="failed", failed_before_start=UNSET_UP),
            "Nomad failed before the task started (Setup Failure).",
        ),
        (allocation(client_status="failed", exit_code="1"), "The task stopped (exit code 1)."),
        (
            allocation(client_status="failed", exit_code="137", oom_killed="true"),
            "Nomad stopped the task for using too much memory (exit code 137).",
        ),
        (
            allocation(description="allocation exceeded max_run_duration", exit_code="137"),
            "Nomad stopped the task at its time limit.",
        ),
        (
            allocation(client_status="lost", desired_status="stop") | {"TaskStates": None},
            "Nomad lost the node running the task.",
        ),
    ],
)
def test_an_endings_details_name_no_host_and_its_message_is_nomads_own(
    allocation_ended: dict[str, Any], details: str
) -> None:
    ended = nomad_answering(lambda request: httpx.Response(200, json=[allocation_ended])).ended("a_1")
    assert ended.details == details
    assert "localhost" not in ended.details
    assert ended.message.startswith(allocation_ended["ClientDescription"])


def test_a_job_nomad_never_allocated_didnt_start() -> None:
    ended = nomad_answering(lambda request: httpx.Response(200, json=[])).ended("a_1")
    assert ended == Ended("a_1", Ending.NOT_STARTED, "Nomad made no allocation.", "No allocation")


def streamed(*events: tuple[str, dict[str, Any]]) -> str:
    """An event stream: a heartbeat, then each event in a frame of its own."""
    frames: list[dict[str, Any]] = [{}] + [
        {"Index": 7, "Events": [{"Topic": topic, "Payload": payload}]} for topic, payload in events
    ]
    return "".join(json.dumps(frame) + "\n" for frame in frames)


def test_changes_are_allocations_and_placements_from_the_event_stream() -> None:
    asked: list[httpx.Request] = []
    body = streamed(
        ("Evaluation", {"Evaluation": {"JobID": "a_1", "Status": "blocked"}}),
        ("Evaluation", {"Evaluation": {"JobID": "a_1", "Status": "complete"}}),
        ("Allocation", {"Allocation": allocation(client_status="pending") | {"TaskStates": None}}),
        ("Allocation", {"Allocation": allocation(client_status="running")}),
        ("Allocation", {"Allocation": allocation(exit_code="0")}),
        ("Allocation", {"Allocation": allocation(desired_status="stop", exit_code="137")}),
        ("Allocation", {"Allocation": allocation(client_status="lost", desired_status="stop")}),
    )

    def respond(request: httpx.Request) -> httpx.Response:
        asked.append(request)
        return httpx.Response(200, text=body)

    changes: list[Job | Ended] = []
    with pytest.raises(NomadUnavailable):
        changes.extend(nomad_answering(respond).changes(41))
    assert [(change.ending if isinstance(change, Ended) else change.state) for change in changes] == [
        JobState.UNPLACED,
        JobState.QUEUED,
        JobState.RUNNING,
        Ending.EXITED,
        Ending.LOST,
    ], "an allocation stopped on request isn't told"
    [request] = asked
    assert request.url.params.get_list("topic") == ["Allocation:*", "Evaluation:*"]
    assert (request.url.params["index"], request.url.params["namespace"]) == ("42", "gradeflow")


def test_nomad_out_of_reach_is_unavailable() -> None:
    nomad = nomad_answering(lambda request: httpx.Response(503))
    with pytest.raises(NomadUnavailable):
        nomad.jobs("a_")
    with pytest.raises(NomadUnavailable):
        nomad.register({"ID": "a_1"})
    with pytest.raises(NomadUnavailable):
        next(nomad.changes(1))
