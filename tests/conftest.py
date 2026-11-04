import base64
import hashlib
import json
import os
import runpy
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from queue import Queue
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import httpx
import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core import MarkedSubmission, MethodFileError, QuestionSet
from gradeflow_core import Question as CoreQuestion
from gradeflow_core.editing import dump_yaml
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.jobs import runs
from gradeflow_app.jobs.payloads import MarkingRun
from gradeflow_app.jobs.runner import Ending, Launch, Listener, Run, RunnerUnavailable, State, id_prefix
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


EXAM_ROWS = [
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


EXAM_CSV = "StudentID,Q1,Q1 Pts,Q2,Q3\n" + "".join(
    f'{encrypted_id(student)},"{q1}",{q1_marks},{q2},{q3}\n' for student, q1, q1_marks, q2, q3 in EXAM_ROWS
)

HTMX_HEADERS = {"HX-Request": "true", "HX-Request-Type": "partial"}


SANDBOX_SCRIPT = Path(__file__).parents[1] / "sandbox" / "sandbox.py"

type Event = dict[str, Any]
type Task = Callable[[dict[str, Any]], Any]


def sandbox(env: dict[str, str] | None = None) -> dict[str, Any]:
    """The globals of the sandbox script, run in the test's process with the job's `env`. Its requests go to the
    app's views."""
    env = env or {"GRADEFLOW_JOB_URL": "http://testserver/", "GRADEFLOW_JOB_SECRET": "test"}
    with patch.dict(os.environ, env):
        namespace: dict[str, Any] = runpy.run_path(str(SANDBOX_SCRIPT))["main"].__globals__
    namespace["CLIENT"] = httpx.Client(transport=_ViewTransport())
    namespace["hide_secret_from_job_code"] = lambda: None  # the test's own process
    return namespace


class _ViewTransport(httpx.BaseTransport):
    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = Client().generic(
            request.method,
            request.url.path,
            request.read(),
            content_type=request.headers.get("Content-Type", "application/octet-stream"),
            headers={"X-GradeFlow-Signature": request.headers["X-GradeFlow-Signature"]},
        )
        return httpx.Response(response.status_code, content=response.content)


def sandbox_tasks(call_back: Callable[[Event], None]) -> dict[str, Task]:
    """The sandbox's tasks, by name, run in the test's process and sending their events to `call_back`."""
    namespace = sandbox()
    namespace["call_back"] = call_back
    tasks: dict[str, Task] = namespace["TASKS"]
    return tasks


def marked_by_sandbox(payload: MarkingRun) -> list[MarkedSubmission]:
    """``payload`` marked by the sandbox."""
    outcome = sandbox_tasks(lambda event: None)["mark"](payload.model_dump(mode="json"))
    return [MarkedSubmission.model_validate(item) for item in outcome]


@dataclass
class FakeRunner:
    """A runner holding the runs started with it. While `runs_jobs`, it runs each as it starts with the sandbox
    script in the test's process; comparisons wait for tests to drive them, as the app has no embedding model."""

    held: dict[str, State] = field(default_factory=dict)
    launches: dict[str, Launch] = field(default_factory=dict)
    stopped: list[str] = field(default_factory=list)
    runs_jobs: bool = True
    is_down: bool = False

    def start(self, run: Run, launch: Launch) -> None:
        self._check_up()
        self.held[run.id] = State.QUEUED
        self.launches[run.id] = launch
        if self.runs_jobs and launch.task != "compare":
            self.run(run.id)

    def stop(self, kind: str, owner: object) -> None:
        self._check_up()
        for run_id in [run_id for run_id in self.held if run_id.startswith(id_prefix(kind, owner))]:
            self.stopped.append(run_id)
            del self.held[run_id]

    def forget(self, run: Run) -> None:
        self._check_up()
        self.stopped.append(run.id)
        self.held.pop(run.id, None)

    def active(self, kind: str, owner: object = "") -> list[tuple[Run, State]]:
        self._check_up()
        return [
            (run, state)
            for run_id, state in self.held.items()
            if run_id.startswith(id_prefix(kind, owner)) and state is not State.ENDED and (run := Run.parse(run_id))
        ]

    def follow(self, listener: Listener) -> None:
        pass

    def run(self, run_id: str) -> None:
        """Run it as a runner would: the sandbox fetches its input and calls back."""
        self.held[run_id] = State.RUNNING
        launch = self.launches[run_id]
        sandbox(launch.environment())["main"](launch.task)

    def end(self, run_id: str, ending: Ending = Ending.FAILED) -> None:
        """End the run without its calling back, telling the app as the worker would."""
        self.held[run_id] = State.ENDED
        run = Run.parse(run_id)
        assert run is not None
        runs.listener.ended(run, ending)

    def run_ids(self, kind: str) -> list[str]:
        """The IDs of the runs of `kind` started, oldest first."""
        return [run_id for run_id in self.launches if run_id.startswith(id_prefix(kind))]

    def _check_up(self) -> None:
        if self.is_down:
            raise RunnerUnavailable


@pytest.fixture(autouse=True)
def fake_runner(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> FakeRunner:
    """Tasks in the test's process, and the runner's runs there too."""
    settings.TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
    fake = FakeRunner()
    monkeypatch.setattr(runs, "runner", lambda: fake)
    return fake


@pytest.fixture
def idle_runner(fake_runner: FakeRunner) -> FakeRunner:
    """The runner, holding runs without running them until a test does."""
    fake_runner.runs_jobs = False
    return fake_runner


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


def upload(client: Client, assessment: Assessment, csv: str = EXAM_CSV, **fields: object) -> Response:
    """Imports `csv`, with `fields` overriding the form's, and runs the marking that follows as the commit would."""
    with TestCase.captureOnCommitCallbacks(execute=True):
        return client.post(
            reverse("import_upload", args=[assessment.pk]),
            {
                "name": "exam.csv",
                "answers_csv": SimpleUploadedFile("answers.csv", csv.encode(), "text/csv"),
                "student_id_column": "StudentID",
                "answer_columns": json.dumps(["Q1", "Q2", "Q3"]),
                "marks_columns": json.dumps({"Q1": "Q1 Pts"}),
                **fields,
            },
        )


@pytest.fixture
def assessment(client: Client) -> Assessment:
    """An assessment with `EXAM_ROWS` imported."""
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


def _run_on_own_connection(action: Callable[[], Any], backend_pids: Queue[int]) -> Any:
    close_old_connections()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SELECT pg_backend_pid()")
            backend_pids.put(cursor.fetchone()[0])
        return action()
    except (ValidationError, MethodFileError) as error:
        return error
    finally:
        connections.close_all()


def _wait_for_database_lock(backend_pid: int, operation: Future[Any]) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with connection.cursor() as cursor:
            cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", [backend_pid])
            row = cursor.fetchone()
        if row and row[0] == "Lock":
            return
        assert not operation.done(), "The competing write finished before the owning transaction committed."
        time.sleep(0.01)
    pytest.fail("The competing connection did not wait for a lock.")


def race(first: Callable[[], Any], second: Callable[[], Any]) -> tuple[Any, Any]:
    """Hold the first operation's real locks until PostgreSQL confirms that the second is blocked."""
    ready, release = threading.Event(), threading.Event()
    first_backend_pids: Queue[int] = Queue()
    second_backend_pids: Queue[int] = Queue()

    def hold_first_locks() -> Any:
        with transaction.atomic():
            result = first()
            ready.set()
            assert release.wait(timeout=10)
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(_run_on_own_connection, hold_first_locks, first_backend_pids)
        try:
            assert ready.wait(timeout=10), (
                first_future.result() if first_future.done() else "First write did not finish."
            )
            second_future = pool.submit(_run_on_own_connection, second, second_backend_pids)
            first_pid, second_pid = first_backend_pids.get(timeout=5), second_backend_pids.get(timeout=5)
            assert first_pid != second_pid
            _wait_for_database_lock(second_pid, second_future)
        finally:
            release.set()
        return first_future.result(timeout=10), second_future.result(timeout=10)


def signature(secret: str, body: bytes) -> str:
    """What the sandbox signs `body` with, by the run's `secret`."""
    sign: Callable[[bytes], str] = sandbox({"GRADEFLOW_JOB_URL": "", "GRADEFLOW_JOB_SECRET": secret})["signature"]
    return sign(body)


def post_callback(run_id: str, event: dict[str, Any], secret: str | None = None) -> int:
    """Send `event` as the run with `run_id` would."""
    run = Run.parse(run_id)
    assert run is not None
    body = json.dumps(event).encode()
    response = Client().post(
        reverse("job_callback", args=[run_id]),
        body,
        content_type="application/json",
        headers={"X-GradeFlow-Signature": signature(secret or runs.secret(run), body)},
    )
    return response.status_code


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
    return MethodWorkspace(method, draft).publish(user)


def publish_light_method(library: Library, user: User, name: str = "light", label: str = "Light") -> MethodVersion:
    return publish_version(Method.objects.create(library=library, name=name), user, method_definition(label))


def create_course(owner: User, name: str = "Chemistry") -> Course:
    course = Course.objects.create(name=name)
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
