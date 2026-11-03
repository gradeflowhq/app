"""Pages that count what they list keep the list's own order."""

import pytest
from django.test import Client
from django.urls import reverse

from gradeflow_app.accounts.models import User
from tests.conftest import create_assessment, publish_light_method

pytestmark = pytest.mark.django_db


def test_the_course_page_lists_its_most_recently_changed_assessment_first(client: Client) -> None:
    course = create_assessment(client, "Quiz 1").course
    client.post(reverse("course", args=[course.pk]), {"name": "Quiz 2", "description": ""})
    page = client.get(reverse("course", args=[course.pk])).content.decode()
    assert page.index("Quiz 2") < page.index("Quiz 1")


def test_a_library_lists_its_methods_by_name(client: Client, user: User) -> None:
    publish_light_method(user.library, user, "b-light", "B light")
    publish_light_method(user.library, user, "a-light", "A light")
    page = client.get(reverse("your_methods")).content.decode()
    assert page.index("A light") < page.index("B light")
