import base64
import hashlib
import hmac
import json
import os
import runpy
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar
from unittest.mock import patch
from uuid import UUID

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core import MarkedSubmission, QuestionSet
from gradeflow_core.editing import dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.callbacks import _receive as receive
from gradeflow_app.jobs.executors import Unavailable
from gradeflow_app.jobs.handlers import handler
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import Payload
from gradeflow_app.libraries import services
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.models import Library, Method, MethodDraft, MethodVersion, VersionTests
from gradeflow_app.libraries.workbench import new_tests
from gradeflow_app.marking.models import Draft, Marking

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as Response


def envelope(student_id: str) -> str:
    """A stand-in for an encrypted student ID: the server only checks the envelope's shape."""
    digest = base64.b64encode(hashlib.sha256(student_id.encode()).digest()).decode()
    return f"enc:v2:{digest[:24]}:{digest[24:40]}:{digest[:20]}"


RESULTS = [
    # student, Q1 (text), Q1 marks, Q2 (number), Q3 (choice)
    ("S001", "photosynthesis uses light", "1", "42", "A"),
    ("S002", "Photosynthesis uses light.", "1", "42.0", "B"),
    ("S003", "light and chlorophyll", "0", "41", "A"),
    ("S004", "respiration", "0", "40", "C"),
    ("S005", "", "0", "", "A"),
    ("S006", "plants make sugar from sunlight", "1", "42", "A"),
    ("S007", "the mitochondria", "0", "39", "B"),
    ("S008", "glucose and oxygen", "0", "42", "A"),
    ("S009", "carbon dioxide is absorbed", "0", "43", "D"),
    ("S010", "chlorophyll absorbs light energy", "1", "42", "A"),
]


RESULTS_CSV = "StudentID,Q1,Q1 Pts,Q2,Q3\n" + "".join(
    f'{envelope(student)},"{q1}",{q1_marks},{q2},{q3}\n' for student, q1, q1_marks, q2, q3 in RESULTS
)

HTMX = {"HX-Request": "true"}


SANDBOX = Path(__file__).parents[1] / "sandbox" / "sandbox.py"

type Event = dict[str, Any]
type Task = Callable[[dict[str, Any]], None]


def sandbox(call_back: Callable[[Event], None]) -> dict[str, Task]:
    """The sandbox's tasks, by name, run in the test's process and sending their events to `call_back`."""
    with patch.dict(os.environ, {"GRADEFLOW_JOB_URL": "http://sandbox.invalid/", "GRADEFLOW_JOB_SECRET": "test"}):
        tasks: dict[str, Task] = runpy.run_path(str(SANDBOX))["TASKS"]
    tasks["mark"].__globals__["call_back"] = call_back
    return tasks


def marked_by_sandbox(payload: Payload) -> list[MarkedSubmission]:
    """``payload`` marked by the sandbox."""
    events: list[Event] = []
    sandbox(events.append)["mark"](payload.model_dump(mode="json"))
    return [MarkedSubmission.model_validate(item) for event in events for item in event["submissions"]]


class Synchronous:
    """Runs jobs in the sandbox's tasks, in the test's process, its events received as the callback receives them.
    Grouping jobs stay queued for tests to drive through their callbacks: the app has no embedding model."""

    def submit(self, job: Job) -> None:
        if job.kind == Job.Kind.GROUPING:
            return
        assert job.input is not None
        task = sandbox(partial(receive, job))[handler(job).task]
        receive(job, {"event": "started"})
        try:
            task(job.input)
        except Exception as error:
            receive(job, {"event": "failed", "error": str(error) or type(error).__name__})
        else:
            receive(job, {"event": "finished"})

    def cancel(self, job: Job) -> None:
        pass


@pytest.fixture(autouse=True)
def _in_process(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks and jobs, in the test's process."""
    settings.TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
    monkeypatch.setattr(queue, "executor", Synchronous)


@pytest.fixture
def user(db: None) -> User:
    return User.objects.create_user("ana@example.edu", "a-long-password", name="Ana Tan")


def signed_in(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def client(user: User) -> Client:
    return signed_in(user)


def create_assessment(client: Client, name: str = "Midterm") -> Assessment:
    """A course and an assessment in it, created as the client's user, who owns them."""
    response = client.post(reverse("courses"), {"name": "BIO1010", "description": ""})
    course = Course.objects.get(pk=response.headers["Location"].split("/")[-2])
    response = client.post(reverse("course", args=[course.pk]), {"name": name, "description": ""})
    assessment = course.assessments.get(name=name)
    assert response.headers["Location"] == reverse("import", args=[assessment.pk])
    return assessment


def upload(client: Client, assessment: Assessment, data: str = RESULTS_CSV, **fields: object) -> Response:
    """Imports `data`, with `fields` overriding the form's, and runs the marking that follows as the commit would."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(
            reverse("upload", args=[assessment.pk]),
            {
                "name": "results.csv",
                "results": SimpleUploadedFile("results", data.encode(), "text/csv"),
                "student_id_column": "StudentID",
                "answer_columns": json.dumps(["Q1", "Q2", "Q3"]),
                "marks_columns": json.dumps({"Q1": "Q1 Pts"}),
                **fields,
            },
        )


@pytest.fixture
def assessment(client: Client) -> Assessment:
    """An assessment with `RESULTS` imported."""
    created = create_assessment(client)
    response = upload(client, created)
    assert response.status_code == 200, response.content
    return created


def start_marking(client: Client, assessment: Assessment, key: str, method: str) -> tuple[str, Draft]:
    """The URL of a new draft marking question `key` with `method`, and the draft."""
    question = assessment.questions.get(key=key)
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]), {"method": method})
    draft = Draft.objects.get(question=question)
    return reverse("draft", args=[assessment.pk, draft.pk]), draft


def code_draft(client: Client, assessment: Assessment) -> tuple[str, dict[str, str]]:
    """The URL of a draft marking Q1 with code, and its fields as the editor posts them."""
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/custom-code@1")
    return url, {"path": "", "fields": "1", "code": "correct = False\n"}


def member(assessment: Assessment, role: Role, email: str = "bo@example.edu") -> Client:
    """A client signed in as a new member of the assessment's course, in `role`."""
    user = User.objects.create_user(email, "another-password")
    Member.objects.create(course=assessment.course, user=user, role=role)
    return signed_in(user)


def full_marks(assessment: Assessment) -> Assessment:
    """The assessment with every question's marking saved as Full Marks, without marking anyone yet."""
    for question in assessment.questions.all():
        definition = dump_yaml({"question": question.key, "uses": "gradeflow/full-marks@1"})
        Marking.objects.create(assessment=assessment, question=question, definition=definition)
    return assessment


def marked(client: Client, course: Course, name: str, keys: list[str]) -> Assessment:
    """A new assessment in `course` with the answers to `keys` imported, each question marked."""
    client.post(reverse("course", args=[course.pk]), {"name": name, "description": ""})
    assessment = course.assessments.get(name=name)
    upload(client, assessment, answer_columns=json.dumps(keys), marks_columns="{}")
    return full_marks(assessment)


def call_back(job: Job, event: dict[str, Any], secret: str | None = None) -> int:
    body = json.dumps(event).encode()
    signature = hmac.new((secret or job.secret).encode(), body, hashlib.sha256).hexdigest()
    response = Client().post(
        reverse("job_callback", args=[job.pk]),
        body,
        content_type="application/json",
        headers={"X-GradeFlow-Signature": signature},
    )
    return response.status_code


class Idle:
    """An executor that leaves jobs queued, as Nomad does until a job calls back."""

    def submit(self, job: Job) -> None:
        pass

    def cancel(self, job: Job) -> None:
        pass


class Waiting:
    """Nomad, receiving jobs without running them until a test ends them or takes it down."""

    submitted: ClassVar[list[UUID]] = []
    cancelled: ClassVar[list[UUID]] = []
    ended: ClassVar[set[UUID]] = set()
    down: ClassVar[bool] = False

    def submit(self, job: Job) -> None:
        if self.down:
            raise Unavailable
        self.submitted.append(job.pk)

    def cancel(self, job: Job) -> None:
        self.cancelled.append(job.pk)

    def received(self) -> dict[UUID, bool]:
        if self.down:
            raise Unavailable
        return {pk: pk in self.ended for pk in self.submitted}


class Sandbox:
    """Takes jobs and leaves them running, calling `submitted` with each as the sandbox gets it."""

    def __init__(self, submitted: Callable[[Job], None]) -> None:
        self.submitted = submitted

    def submit(self, job: Job) -> None:
        self.submitted(job)

    def cancel(self, job: Job) -> None:
        pass


QUESTIONS = QuestionSet.model_validate({"Q1": {"type": "text"}})


def definition(label: str = "Light", answer: str = "light", uses: str = "") -> str:
    check = {"uses": uses} if uses else {"uses": "gradeflow/key-points@1", "with": {"points": [answer]}}
    return dump_yaml({"label": label, "summary": f"Says {answer}.", "question_types": ["text"], "checks": [check]})


def drafted(method: Method, user: User, text: str) -> MethodDraft:
    """The user's draft of `method` with `text`, started from its latest version."""
    return MethodDraft.objects.create(
        method=method,
        user=user,
        based_on=method.latest_version,
        definition=text,
        tests=dict(new_tests(QUESTIONS)),
    )


def published(method: Method, user: User, text: str) -> MethodVersion:
    draft = drafted(method, user, text)
    return services.publish(draft, user, MethodWorkspace(method, draft).core)


def light(library: Library, user: User, name: str = "light", label: str = "Light") -> MethodVersion:
    return published(Method.objects.create(library=library, name=name), user, definition(label))


def course_of(owner: User, name: str = "Chemistry") -> Course:
    course = Course.objects.create(name=name)
    Library.objects.create(namespace=services.free_namespace(name), course=course)
    Member.objects.create(course=course, user=owner, role=Role.OWNER)
    return course


def session(method: Method, number: int | None) -> MethodDraft | VersionTests:
    if number is None:
        return MethodDraft.objects.get(method=method)
    return VersionTests.objects.get(version__method=method, version__number=number)


def url(method: Method, name: str, number: int | None = None) -> str:
    args = [method.pk, *([number] if number is not None else [])]
    return reverse(name, args=args)


def edit(client: Client, method: Method, view: str, number: int | None = None, **data: object) -> Response:
    """Post `data` to the workspace's `view`, from a page showing the workspace as it is."""
    return posted(client, method, view, number, data)


def posted(client: Client, method: Method, view: str, number: int | None, data: dict[str, object]) -> Response:
    revision = session(method, number).revision
    return client.post(url(method, view, number), {"revision": revision, **data}, headers=HTMX)


def publish(client: Client, method: Method) -> Response:
    return edit(client, method, "method_publish")
