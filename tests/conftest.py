import base64
import hashlib
import hmac
import json
import os
import runpy
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections
from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core import MarkedSubmission, QuestionSet
from gradeflow_core import Question as CoreQuestion
from gradeflow_core.editing import dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.jobs import queue
from gradeflow_app.jobs.callbacks import _apply_event as receive
from gradeflow_app.jobs.executors import ExecutorUnavailable
from gradeflow_app.jobs.handlers import handler
from gradeflow_app.jobs.models import Job
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.libraries import services
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.method_tests import new_tests
from gradeflow_app.libraries.models import Library, Method, MethodDraftRecord, MethodVersion, VersionTests
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.questions.models import Question

if TYPE_CHECKING:
    from django.test.client import _MonkeyPatchedWSGIResponse as Response


def encrypted_id(student_id: str) -> str:
    """A stand-in for an encrypted student ID: the server only checks its shape."""
    digest = base64.b64encode(hashlib.sha256(student_id.encode()).digest()).decode()
    return f"enc:v2:{digest[:24]}:{digest[24:40]}:{digest[:20]}"


EXPORT_ROWS = [
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


EXPORT_CSV = "StudentID,Q1,Q1 Pts,Q2,Q3\n" + "".join(
    f'{encrypted_id(student)},"{q1}",{q1_marks},{q2},{q3}\n' for student, q1, q1_marks, q2, q3 in EXPORT_ROWS
)

HTMX_HEADERS = {"HX-Request": "true", "HX-Request-Type": "partial"}


SANDBOX_SCRIPT = Path(__file__).parents[1] / "sandbox" / "sandbox.py"

type Event = dict[str, Any]
type Task = Callable[[dict[str, Any]], None]


def sandbox_tasks(call_back: Callable[[Event], None]) -> dict[str, Task]:
    """The sandbox's tasks, by name, run in the test's process and sending their events to `call_back`."""
    with patch.dict(os.environ, {"GRADEFLOW_JOB_URL": "http://sandbox.invalid/", "GRADEFLOW_JOB_SECRET": "test"}):
        tasks: dict[str, Task] = runpy.run_path(str(SANDBOX_SCRIPT))["TASKS"]
    tasks["mark"].__globals__["call_back"] = call_back
    return tasks


def marked_by_sandbox(payload: MarkingRun) -> list[MarkedSubmission]:
    """``payload`` marked by the sandbox."""
    events: list[Event] = []
    sandbox_tasks(events.append)["mark"](payload.model_dump(mode="json"))
    return [MarkedSubmission.model_validate(item) for event in events for item in event["submissions"]]


class InProcessExecutor:
    """Runs jobs in the sandbox's tasks, in the test's process, its events received as the callback receives them.
    Grouping jobs stay queued for tests to drive through their callbacks: the app has no embedding model."""

    def submit(self, job: Job) -> None:
        if job.kind == Job.Kind.GROUPING:
            return
        assert job.input is not None
        task = sandbox_tasks(partial(receive, job))[handler(job).task]
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
def _run_tasks_in_process(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks and jobs, in the test's process."""
    settings.TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
    monkeypatch.setattr(queue, "executor", InProcessExecutor)


@pytest.fixture
def user(db: None) -> User:
    return User.objects.create_user("ana@example.edu", "a-long-password", name="Ana Tan")


@pytest.fixture
def bo(db: None) -> User:
    """Another person, in no course yet."""
    return User.objects.create_user("bo@example.edu", "another-password", name="Bo Lim")


def signed_in_client(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


@pytest.fixture
def client(user: User) -> Client:
    return signed_in_client(user)


def create_assessment(client: Client, name: str = "Midterm") -> Assessment:
    """A course and an assessment in it, created as the client's user, who owns them."""
    response = client.post(reverse("courses"), {"name": "BIO1010", "description": ""})
    course = Course.objects.get(pk=response.headers["Location"].split("/")[-2])
    response = client.post(reverse("course", args=[course.pk]), {"name": name, "description": ""})
    assessment = course.assessments.get(name=name)
    assert response.headers["Location"] == reverse("import", args=[assessment.pk])
    return assessment


def upload(client: Client, assessment: Assessment, csv: str = EXPORT_CSV, **fields: object) -> Response:
    """Imports `csv`, with `fields` overriding the form's, and runs the marking that follows as the commit would."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(
            reverse("import_upload", args=[assessment.pk]),
            {
                "name": "results.csv",
                "answers_csv": SimpleUploadedFile("answers.csv", csv.encode(), "text/csv"),
                "student_id_column": "StudentID",
                "answer_columns": json.dumps(["Q1", "Q2", "Q3"]),
                "marks_columns": json.dumps({"Q1": "Q1 Pts"}),
                **fields,
            },
        )


@pytest.fixture
def assessment(client: Client) -> Assessment:
    """An assessment with `EXPORT_ROWS` imported."""
    created = create_assessment(client)
    response = upload(client, created)
    assert response.status_code == 200, response.content
    return created


def start_marking(client: Client, assessment: Assessment, key: str, method: str) -> tuple[str, MarkingDraftRecord]:
    """The URL of a new draft marking question `key` with `method`, and the draft."""
    question = assessment.questions.get(key=key)
    client.post(reverse("marking_start", args=[assessment.pk, question.pk]), {"method": method})
    draft = MarkingDraftRecord.objects.get(question=question)
    return reverse("draft", args=[assessment.pk, draft.pk]), draft


def save_draft(client: Client, draft: MarkingDraftRecord) -> Response:
    """Save `draft`, then run what waits on the commit, such as marking."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(
            reverse("draft_save", args=[draft.assessment_id, draft.pk]), {"path": "", "operation": "save"}
        )


def preview_draft(client: Client, draft: MarkingDraftRecord) -> Response:
    """Preview `draft`, then run what waits on the commit, such as the preview's job."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(reverse("draft_preview", args=[draft.assessment_id, draft.pk]))


def code_draft(client: Client, assessment: Assessment) -> tuple[str, dict[str, str]]:
    """The URL of a draft marking Q1 with code, and its fields as the editor posts them."""
    url, _ = start_marking(client, assessment, "Q1", "gradeflow/custom-code@1")
    return url, {"path": "", "fields_posted": "1", "code": "correct = False\n"}


def make_code_question(assessment: Assessment, key: str) -> Question:
    """Question `key` as code, which Code Tests marks."""
    question = assessment.questions.get(key=key)
    question.core_question = CoreQuestion.load({"type": "code", "max_marks": question.core_question.max_marks})
    question.save()
    return question


def member_client(assessment: Assessment, role: Role, email: str = "bo@example.edu") -> Client:
    """A client signed in as a new member of the assessment's course, in `role`."""
    user = User.objects.create_user(email, "another-password")
    Member.objects.create(course=assessment.course, user=user, role=role)
    return signed_in_client(user)


def full_marks_marking(assessment: Assessment, key: str) -> Marking:
    """Question `key`'s marking, saved as Full Marks without marking anyone yet."""
    return Marking.objects.create(
        assessment=assessment,
        question=assessment.questions.get(key=key),
        definition=dump_yaml({"question": key, "uses": "gradeflow/full-marks@1"}),
    )


def save_full_marks_markings(assessment: Assessment) -> Assessment:
    """The assessment with every question's marking saved as Full Marks, without marking anyone yet."""
    for key in assessment.questions.values_list("key", flat=True):
        full_marks_marking(assessment, key)
    return assessment


def assessment_with_full_marks(client: Client, course: Course, name: str, question_keys: list[str]) -> Assessment:
    """A new assessment in `course` with the answers to `question_keys` imported, each question's marking saved as
    Full Marks."""
    client.post(reverse("course", args=[course.pk]), {"name": name, "description": ""})
    assessment = course.assessments.get(name=name)
    upload(client, assessment, answer_columns=json.dumps(question_keys), marks_columns="{}")
    return save_full_marks_markings(assessment)


def between(text: str, start: str, end: str) -> str:
    """The part of `text` after the first `start`, up to the next `end`."""
    return text.split(start, 1)[1].split(end, 1)[0]


def run_together[T](*actions: Callable[[], T]) -> list[T]:
    """What each action returns, the actions started at the same moment, each on a connection of its own."""
    start_together = threading.Barrier(len(actions))

    def run(action: Callable[[], T]) -> T:
        close_old_connections()
        try:
            start_together.wait(timeout=10)
            return action()
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run, actions))


def signature(secret: str, body: bytes) -> str:
    """What the sandbox signs `body` with, by the job's `secret`."""
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def post_callback(job: Job, event: dict[str, Any], secret: str | None = None) -> int:
    body = json.dumps(event).encode()
    response = Client().post(
        reverse("job_callback", args=[job.pk]),
        body,
        content_type="application/json",
        headers={"X-GradeFlow-Signature": signature(secret or job.secret, body)},
    )
    return response.status_code


class IdleExecutor:
    """An executor that leaves jobs queued, as Nomad does until a job calls back."""

    def submit(self, job: Job) -> None:
        pass

    def cancel(self, job: Job) -> None:
        pass


@pytest.fixture
def idle_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(queue, "executor", IdleExecutor)


@dataclass
class FakeNomad:
    """Nomad, receiving jobs without running them until a test ends them or takes it down."""

    submitted: list[UUID] = field(default_factory=list)
    cancelled: list[UUID] = field(default_factory=list)
    ended: set[UUID] = field(default_factory=set)
    is_down: bool = False

    def submit(self, job: Job) -> None:
        if self.is_down:
            raise ExecutorUnavailable
        self.submitted.append(job.pk)

    def cancel(self, job: Job) -> None:
        self.cancelled.append(job.pk)

    def submitted_jobs(self) -> dict[UUID, bool]:
        if self.is_down:
            raise ExecutorUnavailable
        return {pk: pk in self.ended for pk in self.submitted}


@pytest.fixture
def nomad(monkeypatch: pytest.MonkeyPatch) -> FakeNomad:
    """A `FakeNomad` that runs this test's jobs."""
    nomad = FakeNomad()
    monkeypatch.setattr(queue, "executor", lambda: nomad)
    return nomad


class NotifyingExecutor:
    """Takes jobs and leaves them running, calling `submitted` with each as the sandbox gets it."""

    def __init__(self, submitted: Callable[[Job], None]) -> None:
        self.submitted = submitted

    def submit(self, job: Job) -> None:
        self.submitted(job)

    def cancel(self, job: Job) -> None:
        pass


QUESTIONS = QuestionSet.model_validate({"Q1": {"type": "text"}})


def method_definition(label: str = "Light", answer: str = "light", uses: str = "") -> str:
    check = {"uses": uses} if uses else {"uses": "gradeflow/key-points@1", "with": {"points": [answer]}}
    return dump_yaml({"label": label, "summary": f"Says {answer}.", "question_types": ["text"], "checks": [check]})


def create_method_draft(method: Method, user: User, definition: str) -> MethodDraftRecord:
    """The user's draft of `method` with `definition`, started from its latest version."""
    return MethodDraftRecord.objects.create(
        method=method,
        user=user,
        based_on=method.latest_version,
        definition=definition,
        tests=dict(new_tests(QUESTIONS)),
    )


def publish_version(method: Method, user: User, definition: str) -> MethodVersion:
    draft = create_method_draft(method, user, definition)
    return services.publish(draft, user, MethodWorkspace(method, draft).draft)


def publish_light_method(library: Library, user: User, name: str = "light", label: str = "Light") -> MethodVersion:
    return publish_version(Method.objects.create(library=library, name=name), user, method_definition(label))


def create_course(owner: User, name: str = "Chemistry") -> Course:
    course = Course.objects.create(name=name)
    Library.objects.create(namespace=services.available_namespace(name), course=course)
    Member.objects.create(course=course, user=owner, role=Role.OWNER)
    return course


def designer_record(method: Method, number: int | None) -> MethodDraftRecord | VersionTests:
    if number is None:
        return MethodDraftRecord.objects.get(method=method)
    return VersionTests.objects.get(version__method=method, version__number=number)


def designer_url(method: Method, name: str, number: int | None = None) -> str:
    args = [method.pk, *([number] if number is not None else [])]
    return reverse(name, args=args)


def post_to_designer(
    client: Client, method: Method, view: str, number: int | None = None, /, **data: object
) -> Response:
    """Post `data` to the workspace's `view`, from a page showing the workspace as it is."""
    revision = designer_record(method, number).revision
    return client.post(designer_url(method, view, number), {"revision": revision, **data}, headers=HTMX_HEADERS)


def post_publish(client: Client, method: Method) -> Response:
    return post_to_designer(client, method, "method_publish")
