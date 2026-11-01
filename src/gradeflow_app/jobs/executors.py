"""Where jobs run: in Nomad sandbox jobs, never in a web process."""

import logging
from functools import cache
from typing import Protocol
from uuid import UUID

import httpx
from django.conf import settings
from django.urls import reverse

from gradeflow_app.jobs.handlers import handler
from gradeflow_app.jobs.models import Job

_LOG = logging.getLogger(__name__)
PREFIX = "gradeflow-"


class Unavailable(Exception):
    """The executor can't be reached, or refused a job."""


class Executor(Protocol):
    def submit(self, job: Job) -> None:
        """Hand `job` over to run; handing it over again changes nothing. Raises `Unavailable`."""

    def cancel(self, job: Job) -> None: ...

    def received(self) -> dict[UUID, bool]:
        """The jobs handed over, and whether each has ended. Raises `Unavailable`."""


class Nomad:
    """Runs a job in a Nomad batch job, which fetches its input from the app and calls back with the outcome."""

    def __init__(self, url: str, token: str, datacenter: str, namespace: str) -> None:
        self.datacenter = datacenter
        self.namespace = namespace
        self.client = httpx.Client(base_url=url, headers={"X-Nomad-Token": token} if token else {}, timeout=30)

    def submit(self, job: Job) -> None:
        try:
            self.client.post(
                "/v1/jobs", params={"namespace": self.namespace}, json={"Job": self.spec(job)}
            ).raise_for_status()
        except httpx.HTTPError as error:
            raise Unavailable from error

    def cancel(self, job: Job) -> None:
        try:
            self.client.delete(f"/v1/job/{self.name(job)}", params={"namespace": self.namespace, "purge": "true"})
        except httpx.HTTPError:
            _LOG.warning("Couldn't stop Nomad job %s", self.name(job))

    def received(self) -> dict[UUID, bool]:
        try:
            response = self.client.get("/v1/jobs", params={"namespace": self.namespace, "prefix": PREFIX})
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise Unavailable from error
        found = {}
        for listed in response.json():
            try:
                found[UUID(listed["ID"].removeprefix(PREFIX))] = listed["Status"] == "dead"
            except ValueError:
                continue  # not one of our jobs, such as a registry
        return found

    @staticmethod
    def name(job: Job) -> str:
        return f"{PREFIX}{job.pk}"

    def spec(self, job: Job) -> dict[str, object]:
        url = settings.GRADEFLOW_JOB_URL.rstrip("/") + reverse("job_input", args=[job.pk]).removesuffix("input")
        task = handler(job).task
        return {
            "ID": self.name(job),
            "Name": self.name(job),
            "Namespace": self.namespace,
            "Type": "batch",
            "Datacenters": [self.datacenter],
            "Priority": 50 if job.kind == Job.Kind.REMARK else 70,
            "TaskGroups": [
                {
                    "Name": task,
                    "RestartPolicy": {"Attempts": 0, "Mode": "fail"},
                    "ReschedulePolicy": {"Attempts": 0, "Unlimited": False},
                    "Tasks": [
                        {
                            "Name": task,
                            "Driver": "docker",
                            "Config": {
                                "image": job.image,
                                "force_pull": False,
                                "command": "python",
                                "args": ["/app/sandbox.py", task, str(job.time_limit)],
                            },
                            "Env": {"GRADEFLOW_JOB_URL": url, "GRADEFLOW_JOB_SECRET": job.secret},
                            "KillTimeout": 5_000_000_000,
                            "Resources": {"CPU": 1000, "MemoryMB": 1024},
                        }
                    ],
                }
            ],
        }


@cache
def executor() -> Executor:
    nomad = settings.GRADEFLOW_NOMAD
    return Nomad(nomad["url"], nomad["token"], nomad["datacenter"], nomad["namespace"])
