"""A client of Nomad's HTTP API for batch jobs."""

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from operator import itemgetter
from typing import Any

import httpx

_LOG = logging.getLogger(__name__)
WATCH_WAIT = "5m"
"""How long a watch waits for a change before asking again."""


class NomadUnavailable(Exception):
    """Nomad can't be reached, or refused a request."""


class JobState(StrEnum):
    QUEUED = "queued"
    """Waiting for a place or for its image."""
    RUNNING = "running"
    ENDED = "ended"


class Ending(StrEnum):
    """How a job's latest allocation ended."""

    EXITED = "exited"
    """Its task exited with status 0."""
    TIMED_OUT = "timed out"
    OUT_OF_MEMORY = "out of memory"
    FAILED = "failed"
    """A failing exit status, a stop or a lost node."""


@dataclass(frozen=True)
class Job:
    id: str
    state: JobState


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

    def stop(self, job_id: str) -> None:
        """Stop the job and purge it."""
        self._request("DELETE", f"/v1/job/{job_id}", params={"purge": "true"})

    def jobs(self, prefix: str) -> list[Job]:
        return _jobs(self._request("GET", "/v1/jobs", params={"prefix": prefix}))

    def watch(self, prefix: str) -> Iterator[list[Job]]:
        """The jobs whose ID starts with `prefix`, then again after each change."""
        index = "0"
        while True:
            response = self._request(
                "GET", "/v1/jobs", params={"prefix": prefix, "index": index, "wait": WATCH_WAIT}, timeout=None
            )
            if response.headers["X-Nomad-Index"] != index:
                index = response.headers["X-Nomad-Index"]
                yield _jobs(response)

    def ending(self, job_id: str) -> Ending:
        """How the job's latest allocation ended."""
        response = self._request("GET", f"/v1/job/{job_id}/allocations")
        latest = max(response.json(), key=itemgetter("CreateIndex"), default=None)
        if latest is None:
            return Ending.FAILED
        task_events = [event for state in (latest["TaskStates"] or {}).values() for event in state["Events"]]
        _LOG.info(
            "Nomad job %s ended: %s; %s",
            job_id,
            latest["ClientDescription"],
            "; ".join(event["DisplayMessage"] for event in task_events[-3:]),
        )
        if latest["ClientDescription"] == "allocation exceeded max_run_duration":
            return Ending.TIMED_OUT
        if any(event["Details"].get("oom_killed") == "true" for event in task_events):
            return Ending.OUT_OF_MEMORY
        exit_codes = [event["Details"].get("exit_code") for event in task_events if event["Type"] == "Terminated"]
        exited = latest["DesiredStatus"] == "run" and latest["ClientStatus"] == "complete" and exit_codes[-1:] == ["0"]
        return Ending.EXITED if exited else Ending.FAILED

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self.http.request(method, url, **kwargs)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise NomadUnavailable from error
        return response


def _jobs(response: httpx.Response) -> list[Job]:
    return [Job(job["ID"], _state(job)) for job in response.json()]


def _state(job: dict[str, Any]) -> JobState:
    if job["Status"] == "dead":
        return JobState.ENDED
    groups = job["JobSummary"]["Summary"].values()
    return JobState.RUNNING if any(group["Running"] for group in groups) else JobState.QUEUED
