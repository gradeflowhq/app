from typing import Any

import httpx
import pytest

from gradeflow_app.jobs.nomad.client import Ending, Job, JobState, NomadClient, NomadUnavailable


def nomad_answering(respond: Any) -> NomadClient:
    nomad = NomadClient(url="http://nomad", token="secret", namespace="gradeflow")
    nomad.http = httpx.Client(
        base_url="http://nomad",
        headers=nomad.http.headers,
        params=nomad.http.params,
        transport=httpx.MockTransport(respond),
    )
    return nomad


def listed(job_id: str, status: str, running: int = 0) -> dict[str, Any]:
    summary = {"Queued": 0, "Starting": 0, "Running": running, "Complete": 0, "Failed": 0, "Lost": 0, "Unknown": 0}
    return {"ID": job_id, "Status": status, "JobSummary": {"Summary": {"mark": summary}}}


def test_jobs_are_registered_stopped_and_listed_in_the_namespace() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[
                    listed("a.1", "pending"),
                    listed("a.2", "running"),
                    listed("a.3", "running", 1),
                    listed("a.4", "dead"),
                ],
            )
        return httpx.Response(200, json={})

    nomad = nomad_answering(respond)
    nomad.register({"ID": "a.1"})
    nomad.stop("a.1")
    assert nomad.jobs("a.") == [
        Job("a.1", JobState.QUEUED),
        Job("a.2", JobState.QUEUED),
        Job("a.3", JobState.RUNNING),
        Job("a.4", JobState.ENDED),
    ]
    assert [(request.method, request.url.path) for request in requests] == [
        ("POST", "/v1/jobs"),
        ("DELETE", "/v1/job/a.1"),
        ("GET", "/v1/jobs"),
    ]
    assert all(request.url.params["namespace"] == "gradeflow" for request in requests)
    assert all(request.headers["X-Nomad-Token"] == "secret" for request in requests)
    assert requests[1].url.params["purge"] == "true"
    assert requests[2].url.params["prefix"] == "a."


def test_a_watch_yields_the_jobs_as_they_change() -> None:
    indexes = iter(["5", "5", "7"])
    asked: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["index"])
        return httpx.Response(200, json=[listed("a.1", "running", 1)], headers={"X-Nomad-Index": next(indexes)})

    watch = nomad_answering(respond).watch("a.")
    assert next(watch) == [Job("a.1", JobState.RUNNING)]
    assert next(watch) == [Job("a.1", JobState.RUNNING)]
    assert asked == ["0", "5", "5"], "an unchanged index waits again"


def allocation(
    index: int, description: str = "All tasks have completed", *, status: str = "complete", **details: str
) -> dict[str, Any]:
    terminated = {"Type": "Terminated", "DisplayMessage": "Exit Code", "Details": details}
    return {
        "CreateIndex": index,
        "ClientStatus": status,
        "DesiredStatus": "run",
        "ClientDescription": description,
        "TaskStates": {
            "mark": {"Events": [{"Type": "Started", "DisplayMessage": "Task started", "Details": {}}, terminated]}
        },
    }


@pytest.mark.parametrize(
    ("allocations", "ending"),
    [
        ([allocation(1, exit_code="1"), allocation(2, exit_code="0")], Ending.EXITED),
        ([allocation(1, "allocation exceeded max_run_duration", exit_code="137")], Ending.TIMED_OUT),
        ([allocation(1, status="failed", exit_code="137", oom_killed="true")], Ending.OUT_OF_MEMORY),
        ([allocation(1, status="failed", exit_code="1")], Ending.FAILED),
        ([allocation(1, exit_code="0") | {"DesiredStatus": "stop"}], Ending.FAILED),
        ([], Ending.FAILED),
    ],
)
def test_how_a_job_ended_is_its_latest_allocations(allocations: list[dict[str, Any]], ending: Ending) -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/job/a.1/allocations"
        return httpx.Response(200, json=allocations)

    assert nomad_answering(respond).ending("a.1") is ending


def test_nomad_out_of_reach_is_unavailable() -> None:
    nomad = nomad_answering(lambda request: httpx.Response(503))
    with pytest.raises(NomadUnavailable):
        nomad.jobs("a.")
    with pytest.raises(NomadUnavailable):
        nomad.register({"ID": "a.1"})
