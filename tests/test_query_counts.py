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
from tests.conftest import assessment_with_full_marks, create_assessment, publish_light_method, start_marking

pytestmark = pytest.mark.django_db


def query_count(client: Client, url: str) -> int:
    """How many queries opening `url` takes, once anything it caches is cached."""
    client.get(url)
    reset_queries()
    with CaptureQueriesContext(connection) as captured:
        assert client.get(url).status_code == 200
    return len(captured)


def assert_query_count_stays(
    client: Client, url: str, add_two_more: Callable[[], object], django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    """`url` takes no more queries once `add_two_more` adds two more of what it lists."""
    count_before = query_count(client, url)
    add_two_more()
    with django_assert_num_queries(count_before):
        assert client.get(url).status_code == 200


def test_course_cards_query_count_does_not_grow(
    client: Client, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    course = create_assessment(client, "Quiz 1").course
    assessment_with_full_marks(client, course, "Quiz 2", ["Q1", "Q2"])

    def add_two_more() -> None:
        for name in ["Quiz 3", "Quiz 4"]:
            assessment_with_full_marks(client, course, name, ["Q1", "Q2"])

    assert_query_count_stays(client, reverse("courses"), add_two_more, django_assert_num_queries)


def test_course_page_query_count_does_not_grow(
    client: Client, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    course = create_assessment(client, "Quiz 1").course
    assessment_with_full_marks(client, course, "Quiz 2", ["Q1", "Q2"])

    def add_two_more() -> None:
        for name in ["Quiz 3", "Quiz 4"]:
            assessment_with_full_marks(client, course, name, ["Q1", "Q2"])

    assert_query_count_stays(client, reverse("course", args=[course.pk]), add_two_more, django_assert_num_queries)


def test_library_page_query_count_does_not_grow(
    client: Client, user: User, assessment: Assessment, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    """The course's own methods, and those of the person's library its markings use."""

    def use_new_method(library: Library, name: str) -> None:
        version = publish_light_method(library, user, name, name.title())
        Marking.objects.create(assessment=assessment, definition=dump_yaml({"uses": version.ref}))

    use_new_method(assessment.course.library, "ours-1")
    use_new_method(user.library, "mine-1")

    def add_two_more() -> None:
        for number in [2, 3]:
            use_new_method(assessment.course.library, f"ours-{number}")
            use_new_method(user.library, f"mine-{number}")

    assert_query_count_stays(
        client, reverse("course_methods", args=[assessment.course.pk]), add_two_more, django_assert_num_queries
    )


def test_question_list_query_count_does_not_grow(
    client: Client, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    course = create_assessment(client).course
    one_question, three_questions = (
        assessment_with_full_marks(client, course, "One", ["Q1"]),
        assessment_with_full_marks(client, course, "Three", ["Q1", "Q2", "Q3"]),
    )

    def urls(assessment: Assessment) -> list[str]:
        first = assessment.questions.get(key="Q1")
        return [reverse("question", args=[assessment.pk, first.pk]), reverse("question_list", args=[assessment.pk])]

    for one_question_url, three_questions_url in zip(urls(one_question), urls(three_questions), strict=True):
        expected = query_count(client, one_question_url)
        client.get(three_questions_url)
        with django_assert_num_queries(expected):
            assert client.get(three_questions_url).status_code == 200


def test_question_list_of_drafts_query_count_does_not_grow(
    client: Client, assessment: Assessment, django_assert_num_queries: DjangoAssertNumQueries
) -> None:
    start_marking(client, assessment, "Q1", "gradeflow/custom-code@1")

    def add_two_more() -> None:
        for key in ["Q2", "Q3"]:
            start_marking(client, assessment, key, "gradeflow/custom-code@1")

    assert_query_count_stays(
        client, reverse("question_list", args=[assessment.pk]), add_two_more, django_assert_num_queries
    )
