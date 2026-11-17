"""A client of Nomad's HTTP API for batch jobs."""

import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from operator import itemgetter
from typing import Any

import httpx

JOB_VARIABLE_PATH = "nomad/jobs/"
"""The path of each job's variable, before the job's ID."""
STREAM_TIMEOUT = 60
"""Seconds the event stream may stay silent; Nomad sends a heartbeat every 30."""


class NomadUnavailable(Exception):
    """Nomad can't be reached, or refused a request."""


class JobState(StrEnum):
    QUEUED = "queued"
    """Placed or being placed, and not running yet."""
    UNPLACED = "unplaced"
    """Nomad couldn't place it yet."""
    RUNNING = "running"
    ENDED = "ended"


class Placement(StrEnum):
    """Why Nomad couldn't place a job."""

    NO_NODE = "no node"
    """No node fits it: none is in its datacenter or pool, or each was filtered out."""
    NO_ROOM = "no room"
    """The nodes that fit it are full."""


class Ending(StrEnum):
    """How a job's allocation ended."""

    EXITED = "exited"
    """Its task exited with status 0."""
    NOT_STARTED = "not started"
    """Before its task started, such as when its image couldn't be pulled."""
    TIMED_OUT = "timed out"
    OUT_OF_MEMORY = "out of memory"
    LOST = "lost"
    """With its node."""
    FAILED = "failed"


@dataclass(frozen=True)
class Job:
    id: str
    state: JobState


@dataclass(frozen=True)
class Ended:
    job_id: str
    ending: Ending
    details: str
    """A sentence from Nomad's fields that name no node, image or host."""
    message: str
    """Nomad's own words, which may name them."""


class NomadClient:
    """Each method raises `NomadUnavailable` when Nomad can't be reached or refuses the request."""

    def __init__(self, url: str, token: str, namespace: str) -> None:
        self.http = httpx.Client(
            base_url=url,
            headers={"X-Nomad-Token": token} if token else {},
            params={"namespace": namespace},
            timeout=httpx.Timeout(30, connect=5),
        )

    def register(self, spec: dict[str, Any]) -> None:
        self._request("POST", "/v1/jobs", json={"Job": spec})

    def put_variable(self, job_id: str, items: dict[str, str]) -> None:
        self._request("PUT", f"/v1/var/{JOB_VARIABLE_PATH}{job_id}", json={"Items": items})

    def stop(self, job_id: str) -> None:
        """Stop the job and purge it with its variable."""
        self._request("DELETE", f"/v1/job/{job_id}", params={"purge": "true"})
        self.delete_variable(job_id)

    def delete_variable(self, job_id: str) -> None:
        self._request("DELETE", f"/v1/var/{JOB_VARIABLE_PATH}{job_id}")

    def variable_ids(self, prefix: str) -> list[str]:
        """The job IDs starting with `prefix` that have a variable, even if the job is gone."""
        response = self._request("GET", "/v1/vars", params={"prefix": JOB_VARIABLE_PATH + prefix})
        return [variable["Path"].removeprefix(JOB_VARIABLE_PATH) for variable in response.json()]

    def jobs(self, prefix: str) -> tuple[list[Job], int]:
        """The jobs whose ID starts with `prefix`, and the index of the listing."""
        response = self._request("GET", "/v1/jobs", params={"prefix": prefix})
        return [Job(job["ID"], _state(job)) for job in response.json()], int(response.headers["X-Nomad-Index"])

    def placement(self, job_id: str) -> Placement | None:
        """Why Nomad couldn't place the job, by its latest evaluation that failed to."""
        response = self._request("GET", f"/v1/job/{job_id}/evaluations")
        failed = [evaluation for evaluation in response.json() if evaluation.get("FailedTGAllocs")]
        if not failed:
            return None
        metrics = max(failed, key=itemgetter("CreateIndex"))["FailedTGAllocs"].values()
        return Placement.NO_ROOM if any(metric["NodesExhausted"] for metric in metrics) else Placement.NO_NODE

    def ended(self, job_id: str) -> Ended:
        """How the job's allocation ended."""
        allocations = self._request("GET", f"/v1/job/{job_id}/allocations").json()
        latest = max(allocations, key=itemgetter("CreateIndex"), default=None)
        if latest is None:
            return Ended(job_id, Ending.NOT_STARTED, "Nomad made no allocation.", "No allocation")
        return _ended(latest)

    def changes(self, index: int) -> Iterator[Job | Ended]:
        """Each change to a job after `index`: its allocation queued or running, how it ended, or Nomad failing to
        place it. Allocations stopped on request are left out. Raises `NomadUnavailable` once the stream ends."""
        params = httpx.QueryParams({"topic": ["Allocation:*", "Evaluation:*"], "index": index + 1})
        try:
            with self.http.stream(
                "GET", "/v1/event/stream", params=params, timeout=httpx.Timeout(STREAM_TIMEOUT, connect=5)
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    for event in json.loads(line).get("Events", []) if line else []:
                        if (change := _change(event)) is not None:
                            yield change
        except httpx.HTTPError as error:
            raise NomadUnavailable from error
        raise NomadUnavailable("The event stream ended.")

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.http.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise NomadUnavailable from error
        return response


def _state(job: dict[str, Any]) -> JobState:
    if job["Status"] == "dead":
        return JobState.ENDED
    groups = job["JobSummary"]["Summary"].values()
    if any(group["Running"] for group in groups):
        return JobState.RUNNING
    return JobState.UNPLACED if any(group["Queued"] for group in groups) else JobState.QUEUED


def _change(event: dict[str, Any]) -> Job | Ended | None:
    match event["Topic"], event["Payload"]:
        case "Evaluation", {"Evaluation": {"Status": "blocked", "JobID": job_id}}:
            return Job(job_id, JobState.UNPLACED)
        case "Allocation", {"Allocation": allocation}:
            return _allocation_change(allocation)
        case _:
            return None


def _allocation_change(allocation: dict[str, Any]) -> Job | Ended | None:
    match allocation["ClientStatus"], allocation["DesiredStatus"]:
        case "lost", _:
            return _ended(allocation)
        case _, desired if desired != "run":
            return None
        case "pending", _:
            return Job(allocation["JobID"], JobState.QUEUED)
        case "running", _:
            return Job(allocation["JobID"], JobState.RUNNING)
        case "complete" | "failed", _:
            return _ended(allocation)
        case _:
            return None


def _ended(allocation: dict[str, Any]) -> Ended:
    events = [event for task in (allocation.get("TaskStates") or {}).values() for event in task["Events"]]
    ending = _ending(allocation, events)
    message = "; ".join(
        [
            allocation.get("ClientDescription", ""),
            *(f"{event['Type']}: {event['DisplayMessage']}" for event in events[-3:]),
        ]
    )
    return Ended(allocation["JobID"], ending, _details(events, ending), message)


def _ending(allocation: dict[str, Any], events: list[dict[str, Any]]) -> Ending:
    if allocation["ClientStatus"] == "lost":
        return Ending.LOST
    if allocation.get("ClientDescription") == "allocation exceeded max_run_duration":
        return Ending.TIMED_OUT
    if not any(event["Type"] == "Started" for event in events):
        return Ending.NOT_STARTED
    if any(event["Details"].get("oom_killed") == "true" for event in events):
        return Ending.OUT_OF_MEMORY
    exit_codes = [event["Details"].get("exit_code") for event in events if event["Type"] == "Terminated"]
    exited = allocation["ClientStatus"] == "complete" and exit_codes[-1:] == ["0"]
    return Ending.EXITED if exited else Ending.FAILED


def _is_failed_pull(event: dict[str, Any]) -> bool:
    """Whether the Docker driver failed to pull the task's image."""
    return event["Type"] == "Driver Failure" and str(event["DisplayMessage"]).startswith("Failed to pull")


def _details(events: list[dict[str, Any]], ending: Ending) -> str:
    """How the task ended in a sentence from Nomad's fields, never its messages."""
    exit_codes = [event["Details"]["exit_code"] for event in events if event["Details"].get("exit_code")]
    exit_code = f" (exit code {exit_codes[-1]})" if exit_codes else ""
    failures = [event["Type"] for event in events if event.get("FailsTask") and event["Type"] != "Not Restarting"]
    failure = f" ({failures[0]})" if failures else ""
    not_started = (
        "Nomad couldn't pull the image."
        if any(map(_is_failed_pull, events))
        else f"Nomad failed before the task started{failure}."
    )
    return {
        Ending.EXITED: "The task exited with code 0.",
        Ending.NOT_STARTED: not_started,
        Ending.TIMED_OUT: "Nomad stopped the task at its time limit.",
        Ending.OUT_OF_MEMORY: f"Nomad stopped the task for using too much memory{exit_code}.",
        Ending.LOST: "Nomad lost the node running the task.",
        Ending.FAILED: f"The task stopped{exit_code}.",
    }[ending]
