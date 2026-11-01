"""Pages listing assessments, methods or questions read them in as many queries for three as for one."""

from collections.abc import Callable

import pytest
from django.db import connection, reset_queries
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from gradeflow_core.editing import dump_yaml
from pytest_django import DjangoAssertNumQueries

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.libraries.models import Library
from gradeflow_app.marking.models import Marking
from tests.conftest import create_assessment, light, marked

pytestmark = pytest.mark.django_db


def queries(client: Client, url: str) -> int:
    """How many queries opening `url` takes, once anything it caches is cached."""
    client.get(url)
    reset_queries()
    with CaptureQueriesContext(connection) as captured:
        assert client.get(url).status_code == 200
    return len(captured)


def assert_flat(
    client: Client, url: str, add: Callable[[], object], django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    """`url` takes no more queries once `add` adds two more of what it lists."""
    one = queries(client, url)
    add()
    with django_assert_num_queries(one):
        assert client.get(url).status_code == 200


def test_course_cards(client: Client, django_assert_num_queries: DjangoAssertNumQueries) -> None:
    course = create_assessment(client, "Quiz 1").course
    marked(client, course, "Quiz 2", ["Q1", "Q2"])

    def add() -> None:
        for name in ["Quiz 3", "Quiz 4"]:
            marked(client, course, name, ["Q1", "Q2"])

    assert_flat(client, reverse("courses"), add, django_assert_num_queries)


def test_course_page(client: Client, django_assert_num_queries: DjangoAssertNumQueries) -> None:
    course = create_assessment(client, "Quiz 1").course
    marked(client, course, "Quiz 2", ["Q1", "Q2"])

    def add() -> None:
        for name in ["Quiz 3", "Quiz 4"]:
            marked(client, course, name, ["Q1", "Q2"])

    assert_flat(client, reverse("course", args=[course.pk]), add, django_assert_num_queries)


def test_library_page(
    client: Client, user: User, assessment: Assessment, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    """The course's own methods, and those of the person's library its markings use."""

    def used(library: Library, name: str) -> None:
        version = light(library, user, name, name.title())
        Marking.objects.create(assessment=assessment, definition=dump_yaml({"uses": version.ref}))

    used(assessment.course.library, "ours-1")
    used(user.library, "mine-1")

    def add() -> None:
        for number in [2, 3]:
            used(assessment.course.library, f"ours-{number}")
            used(user.library, f"mine-{number}")

    assert_flat(client, reverse("course_methods", args=[assessment.course.pk]), add, django_assert_num_queries)


def test_question_list(client: Client, django_assert_num_queries: DjangoAssertNumQueries) -> None:
    course = create_assessment(client).course
    one, three = marked(client, course, "One", ["Q1"]), marked(client, course, "Three", ["Q1", "Q2", "Q3"])

    def urls(assessment: Assessment) -> list[str]:
        first = assessment.questions.get(key="Q1")
        return [reverse("question", args=[assessment.pk, first.pk]), reverse("question_list", args=[assessment.pk])]

    for url, same in zip(urls(one), urls(three), strict=True):
        expected = queries(client, url)
        client.get(same)
        with django_assert_num_queries(expected):
            assert client.get(same).status_code == 200
