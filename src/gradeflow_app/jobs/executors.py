"""Where jobs run: in Nomad sandbox jobs, never in a web process."""

import logging
from functools import cache
from uuid import UUID

import httpx
from django.conf import settings
from django.urls import reverse

from gradeflow_app.jobs.handlers import handler
from gradeflow_app.jobs.models import Job

_LOG = logging.getLogger(__name__)
NOMAD_JOB_PREFIX = "gradeflow-"


class ExecutorUnavailable(Exception):
    """The executor can't be reached, or refused a job."""


class Nomad:
    """Runs a job in a Nomad batch job, which fetches its input from the app and calls back with the outcome."""

    def __init__(self, url: str, token: str, datacenter: str, namespace: str) -> None:
        self.datacenter = datacenter
        self.namespace = namespace
        self.client = httpx.Client(
            base_url=url, headers={"X-Nomad-Token": token} if token else {}, timeout=httpx.Timeout(30, connect=5)
        )

    def submit(self, job: Job) -> None:
        """Hand `job` over to run; handing it over again changes nothing. Raises `ExecutorUnavailable`."""
        try:
            self.client.post(
                "/v1/jobs", params={"namespace": self.namespace}, json={"Job": self.nomad_spec(job)}
            ).raise_for_status()
        except httpx.HTTPError as error:
            raise ExecutorUnavailable from error

    def cancel(self, job: Job) -> None:
        try:
            self.client.delete(
                f"/v1/job/{self.nomad_job_id(job)}", params={"namespace": self.namespace, "purge": "true"}
            )
        except httpx.HTTPError:
            _LOG.warning("Couldn't stop Nomad job %s", self.nomad_job_id(job))

    def submitted_jobs(self) -> dict[UUID, bool]:
        """The jobs handed over, and whether each has ended. Raises `ExecutorUnavailable`."""
        try:
            response = self.client.get("/v1/jobs", params={"namespace": self.namespace, "prefix": NOMAD_JOB_PREFIX})
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ExecutorUnavailable from error
        ended_by_job = {}
        for nomad_job in response.json():
            try:
                ended_by_job[UUID(nomad_job["ID"].removeprefix(NOMAD_JOB_PREFIX))] = nomad_job["Status"] == "dead"
            except ValueError:
                continue  # not one of our jobs, such as a registry
        return ended_by_job

    @staticmethod
    def nomad_job_id(job: Job) -> str:
        return f"{NOMAD_JOB_PREFIX}{job.pk}"

    def nomad_spec(self, job: Job) -> dict[str, object]:
        url = settings.GRADEFLOW_APP_URL_FOR_JOBS.rstrip("/") + reverse("job_input", args=[job.pk]).removesuffix(
            "input"
        )
        task = handler(job).task
        return {
            "ID": self.nomad_job_id(job),
            "Name": self.nomad_job_id(job),
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
def executor() -> Nomad:
    nomad = settings.GRADEFLOW_NOMAD
    return Nomad(nomad["url"], nomad["token"], nomad["datacenter"], nomad["namespace"])
