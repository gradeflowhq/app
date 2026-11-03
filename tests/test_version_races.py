"""Publishing, saving markings and deleting methods on separate PostgreSQL connections; and saved markings the core
no longer accepts."""

import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue
from typing import Any

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection, connections, transaction
from django.test import Client
from django.urls import reverse
from gradeflow_core import MethodFileError
from gradeflow_core.editing import dump_yaml, load_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.jobs import queue
from gradeflow_app.libraries import services as library_services
from gradeflow_app.libraries.collection import usable
from gradeflow_app.libraries.models import Library, Method, MethodVersion
from gradeflow_app.marking.checks import assessment_checks
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.questions.models import Question
from tests.conftest import FakeNomad, create_course, method_definition, publish_version, signed_in_client

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def course(user: User, monkeypatch: pytest.MonkeyPatch) -> Course:
    monkeypatch.setattr(queue, "executor", FakeNomad)
    return create_course(user, "Version races")


@pytest.fixture
def version(course: Course) -> MethodVersion:
    method = Method.objects.create(library=course.library, name="source")
    return publish_version(method, course.members.get(role=Role.OWNER).user, method_definition())


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
            state = cursor.fetchone()
        if state and state[0] == "Lock":
            return
        assert not operation.done(), "The competing write finished before the owning transaction committed."
        time.sleep(0.01)
    pytest.fail("The competing connection did not wait for a lock.")


def _race(first: Callable[[], Any], second: Callable[[], Any]) -> tuple[Any, Any]:
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


def _publisher(method: Method, user: User, definition: str) -> Callable[[], MethodVersion]:
    return lambda: publish_version(Method.objects.get(pk=method.pk), user, definition)


@pytest.mark.parametrize("deletion_first", [False, True])
def test_publication_and_deletion_cannot_create_a_dangling_dependency(
    course: Course, version: MethodVersion, user: User, deletion_first: bool
) -> None:
    parent = Method.objects.create(library=course.library, name="parent")
    publish = _publisher(parent, user, method_definition("Parent", uses=version.ref))

    def delete() -> None:
        library_services.delete(Method.objects.get(pk=version.method_id), user)

    if deletion_first:
        removed, outcome = _race(delete, publish)
        assert removed is None
        assert isinstance(outcome, (ValidationError, MethodFileError))
        assert not parent.versions.exists()
        assert not MethodVersion.objects.filter(pk=version.pk).exists()
    else:
        outcome, removed = _race(publish, delete)
        assert isinstance(outcome, MethodVersion)
        assert isinstance(removed, ValidationError)
        assert MethodVersion.objects.filter(pk=version.pk).exists()


@pytest.mark.parametrize("deletion_first", [False, True])
def test_saving_a_marking_and_deleting_its_method_serialize(
    course: Course, version: MethodVersion, user: User, deletion_first: bool
) -> None:
    draft = _create_draft(course, user)
    client = signed_in_client(user)
    definition = dump_yaml({"question": "Q1", "uses": version.ref})
    yaml_url = reverse("draft_yaml", args=[draft.assessment_id, draft.pk])
    save_url = reverse("draft_save", args=[draft.assessment_id, draft.pk])

    def save() -> tuple[int, int]:
        edited = client.post(yaml_url, {"definition": definition})
        saved = client.post(save_url, {})
        return edited.status_code, saved.status_code

    def delete() -> None:
        library_services.delete(Method.objects.get(pk=version.method_id), user)

    if deletion_first:
        removed, responses = _race(delete, save)
        assert removed is None
        assert responses == (200, 200)
        marking = Marking.objects.get(assessment=draft.assessment)
        assert not marking.used_versions.exists()
        assert assessment_checks(draft.assessment) == {}, "naming a deleted version, it marks nothing until fixed"
        assert not MethodVersion.objects.filter(pk=version.pk).exists()
    else:
        responses, removed = _race(save, delete)
        assert responses == (200, 200)
        assert isinstance(removed, ValidationError)
        marking = Marking.objects.get(assessment=draft.assessment)
        assert version.ref in marking.definition
        assert MethodVersion.objects.filter(pk=version.pk).exists()


@pytest.mark.parametrize("rename_first", [False, True])
def test_renaming_a_course_and_saving_a_marking_that_uses_its_method_serialize(
    course: Course, version: MethodVersion, user: User, rename_first: bool
) -> None:
    draft = _create_draft(course, user)
    client = signed_in_client(user)
    definition = dump_yaml({"question": "Q1", "uses": version.ref})
    yaml_url = reverse("draft_yaml", args=[draft.assessment_id, draft.pk])
    save_url = reverse("draft_save", args=[draft.assessment_id, draft.pk])

    def save() -> tuple[int, int]:
        edited = client.post(yaml_url, {"definition": definition})
        saved = client.post(save_url, {})
        return edited.status_code, saved.status_code

    def rename() -> None:
        library_services.rename_namespace(Library.objects.get(course=course), "Renamed races")

    if rename_first:
        _, responses = _race(rename, save)
        assert responses == (200, 200)
        assert Library.objects.get(course=course).namespace == "renamed-races"
        marking = Marking.objects.get(assessment=draft.assessment)
        assert not marking.used_versions.exists(), "naming a renamed version, it marks nothing until fixed"
    else:
        responses, _ = _race(save, rename)
        assert responses == (200, 200)
        assert Library.objects.get(course=course).namespace == "version-races"
        marking = Marking.objects.get(assessment=draft.assessment)
        assert [item.ref for item in marking.used_versions.all()] == [version.ref]


def _create_draft(course: Course, user: User) -> MarkingDraftRecord:
    assessment = Assessment.objects.create(course=course, name="Assessment")
    question = Question.objects.create(assessment=assessment, key="Q1", position=1, definition={"type": "text"})
    return MarkingDraftRecord.objects.create(
        assessment=assessment,
        question=question,
        user=user,
        definition=dump_yaml({"question": "Q1", "uses": "gradeflow/full-marks@1"}),
    )


@pytest.fixture
def invalid_check(course: Course) -> dict[str, Any]:
    method = Method.objects.create(library=course.library, name="conflicting")
    version = publish_version(
        method,
        course.members.get(role=Role.OWNER).user,
        """label: Text input
summary: A text input that rejects nontext settings.
question_types: [text]
inputs:
  threshold: {default: light}
checks:
  - uses: gradeflow/key-point@1
    with: {point: "{{ inputs.threshold }}"}
""",
    )
    return {"question": "Q1", "uses": version.ref, "with": {"threshold": ["abc"]}}


@pytest.mark.parametrize("in_question_group", [False, True])
def test_generated_invalid_settings_remain_a_repairable_draft(
    client: Client, course: Course, user: User, in_question_group: bool, invalid_check: dict[str, Any]
) -> None:
    draft = _create_draft(course, user)
    definition_tree = (
        {"uses": "gradeflow/combination@1", "with": {"checks": [invalid_check]}} if in_question_group else invalid_check
    )
    draft.definition = dump_yaml(definition_tree)
    if in_question_group:
        draft.question = None
    draft.save()
    assert MarkingWorkspace(draft).problems
    url = (
        reverse("question_group_draft", args=[draft.assessment_id, draft.pk])
        if in_question_group
        else reverse("question", args=[draft.assessment_id, draft.question_id])
    )
    assert client.get(url).status_code == 200
    draft.refresh_from_db()
    assert load_yaml(draft.definition) == definition_tree


@pytest.mark.parametrize("in_question_group", [False, True])
def test_saved_invalid_questions_groups_and_history_remain_readable(
    client: Client, course: Course, user: User, in_question_group: bool, invalid_check: dict[str, Any]
) -> None:
    draft = _create_draft(course, user)
    definition_tree = (
        {"uses": "gradeflow/combination@1", "with": {"checks": [invalid_check]}} if in_question_group else invalid_check
    )
    marking = Marking.objects.create(
        assessment=draft.assessment,
        question=None if in_question_group else draft.question,
        definition=dump_yaml(definition_tree),
        questions_marked=["Q1"],
    )
    assessment, question_id = draft.assessment, draft.question_id
    draft.delete()
    assert assessment_checks(assessment) == {}
    urls = [reverse("question", args=[assessment.pk, question_id])]
    urls.append(
        reverse("question_group", args=[assessment.pk, marking.pk])
        if in_question_group
        else reverse("marking_history", args=[assessment.pk, question_id])
    )
    for url in urls:
        assert client.get(url).status_code == 200
    marking.refresh_from_db()
    assert load_yaml(marking.definition) == definition_tree
    assert marking.history.get().definition == marking.definition


def test_an_invalid_saved_question_group_can_be_removed_without_losing_adjustments(
    client: Client, course: Course, user: User, invalid_check: dict[str, Any]
) -> None:
    from gradeflow_app.imports.models import Student
    from gradeflow_app.results.models import Adjustment, Result

    draft = _create_draft(course, user)
    assert draft.question is not None
    marking = Marking.objects.create(
        assessment=draft.assessment,
        definition=dump_yaml({"uses": "gradeflow/combination@1", "with": {"checks": [invalid_check]}}),
        questions_marked=["Q1"],
    )
    student = Student.objects.create(assessment=draft.assessment, encrypted_id="student", answers={"Q1": "x"})
    Result.objects.create(marking=marking, question=draft.question, student=student, proportion=1, revision=0)
    adjustment = Adjustment.objects.create(question=draft.question, student=student, marks=0.5)
    url = reverse("question_group_remove", args=[draft.assessment_id, marking.pk])
    assert client.post(url).status_code == 302
    assert not Marking.objects.filter(pk=marking.pk).exists()
    assert not Result.objects.filter(student=student).exists()
    adjustment.refresh_from_db()
    assert adjustment.marks == 0.5


def test_an_invalid_question_group_keeps_its_questions_when_another_draft_is_saved(
    client: Client, course: Course, user: User, invalid_check: dict[str, Any]
) -> None:
    from gradeflow_app.jobs.models import Job

    draft = _create_draft(course, user)
    original = dump_yaml({"uses": "gradeflow/combination@1", "with": {"checks": [invalid_check]}})
    owning_marking = Marking.objects.create(assessment=draft.assessment, definition=original, questions_marked=["Q1"])
    response = client.post(reverse("draft_save", args=[draft.assessment_id, draft.pk]), {})
    assert response.status_code == 200
    assert b"Q1 has another marking" in response.content
    assert list(draft.assessment.markings.values_list("pk", flat=True)) == [owning_marking.pk]
    assert MarkingDraftRecord.objects.filter(pk=draft.pk).exists()
    assert not Job.objects.filter(assessment=draft.assessment).exists()
    owning_marking.refresh_from_db()
    assert owning_marking.definition == original
    assert owning_marking.questions_marked == ["Q1"]


def test_concurrent_publications_of_one_version_publish_once(course: Course, version: MethodVersion) -> None:
    owner = course.members.get(role=Role.OWNER).user
    other = User.objects.create_user("bo@example.edu", "another-password")
    Member.objects.create(course=course, user=other, role=Role.EDITOR)
    first = _publisher(version.method, owner, method_definition(answer="first"))
    second = _publisher(version.method, other, method_definition(answer="second"))
    published_version, refused = _race(first, second)
    assert isinstance(published_version, MethodVersion)
    assert isinstance(refused, ValidationError)
    assert "A newer version was published" in refused.messages[0]
    method = Method.objects.get(pk=version.method_id)
    assert method.next_version_number == 3
    assert set(method.versions.values_list("number", flat=True)) == {1, 2}
    assert published_version.ref in {item.ref for item in usable(owner, course)}
