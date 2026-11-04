import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from django.urls import reverse
from pytest_django.live_server_helper import LiveServer

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.results.models import Result
from tests.conftest import SANDBOX_SCRIPT, full_marks_marking, sandbox


class App:
    """The app as the sandbox reaches it, answering each request with the next of `replies`."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *replies: httpx.Response | httpx.TransportError) -> None:
        self.replies = iter(replies)
        self.requests: list[httpx.Request] = []
        self.waits: list[float] = []
        namespace = sandbox()
        namespace["CLIENT"] = httpx.Client(transport=httpx.MockTransport(self.reply))
        monkeypatch.setattr(namespace["time"], "sleep", self.waits.append)
        self.call_back: Callable[[dict[str, Any]], None] = namespace["call_back"]

    def reply(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if isinstance(reply := next(self.replies), httpx.TransportError):
            raise reply
        return reply


def test_a_callback_is_sent_again_while_the_app_cant_take_it(monkeypatch: pytest.MonkeyPatch) -> None:
    app = App(
        monkeypatch, httpx.ConnectError("Restarting"), httpx.Response(503), httpx.Response(429), httpx.Response(204)
    )
    app.call_back({"event": "started"})
    assert len(app.requests) == 4
    assert [0 <= wait <= 2**attempt for attempt, wait in enumerate(app.waits, 1)] == [True] * 3


@pytest.mark.parametrize(("status", "attempts"), [(400, 1), (502, 6)])
def test_a_callback_stops_when_refused_or_out_of_tries(
    monkeypatch: pytest.MonkeyPatch, status: int, attempts: int
) -> None:
    app = App(monkeypatch, *[httpx.Response(status)] * 6)
    with pytest.raises(httpx.HTTPStatusError):
        app.call_back({"event": "started"})
    assert len(app.requests) == attempts


@pytest.mark.django_db(transaction=True)
def test_the_sandbox_marks_a_job_through_the_apps_job_urls(
    live_server: LiveServer, assessment: Assessment, user: User, idle_executor: None
) -> None:
    job = schedule_marking(full_marks_marking(assessment, "Q1"), user)
    assert job is not None
    url = live_server.url + reverse("job_input", args=[job.pk]).removesuffix("input")
    environ = os.environ | {"GRADEFLOW_JOB_URL": url, "GRADEFLOW_JOB_SECRET": job.secret}
    sandbox_run = subprocess.run([sys.executable, SANDBOX_SCRIPT, "mark", "60"], env=environ, check=False, timeout=60)
    assert sandbox_run.returncode == 0
    job.refresh_from_db()
    assert (job.status, job.error) == (Job.Status.COMPLETED, "")
    assert Result.objects.filter(question__key="Q1").count() == assessment.students.count()
