"""Saved versions and markings the current core can't read: the rest keeps working, and each shows what is wrong."""

import logging
import re
from html import unescape
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course
from gradeflow_app.libraries.collection import _log_failure, load_saved, loaded_usable_methods
from gradeflow_app.libraries.models import Method, MethodVersion
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.questions.models import Question
from tests.conftest import HTMX_HEADERS, create_course, publish_light_method

pytestmark = pytest.mark.django_db

OLD_PROBLEMS = [
    "Line 11: Key Points has no setting evaluate_expected.",
    "Line 12: There's no method gradeflow/best-answer@1.",
]
OLD_DEFINITION = dump_yaml(
    {
        "version": 2,
        "label": "Light",
        "summary": "Says light.",
        "question_types": ["text"],
        "steps": [
            {"uses": "gradeflow/key-points@1", "with": {"points": ["light"], "evaluate_expected": False}},
            {"uses": "gradeflow/best-answer@1"},
        ],
    }
)


def bullets(page: str) -> list[str]:
    return [unescape(item) for item in re.findall(r"<li>([^<]*)</li>", page)]


@pytest.fixture
def course(user: User) -> Course:
    return create_course(user, "Method laboratory")


@pytest.fixture
def light(course: Course, user: User) -> Method:
    """`light`, whose version 1 loads and whose version 2 was saved by an earlier core."""
    first = publish_light_method(course.library, user)
    method = first.method
    MethodVersion.objects.create(method=method, number=2, definition=OLD_DEFINITION, tests=first.tests, created_by=user)
    return method


@pytest.fixture
def wrapper(course: Course, user: User, light: Method) -> Method:
    """A method whose only version uses `light` version 2."""
    method = Method.objects.create(library=course.library, name="wrapper")
    old_light = MethodVersion.objects.get(method=light, number=2)
    definition = dump_yaml(
        {
            "version": 1,
            "label": "Wrapper",
            "summary": "Uses Light.",
            "question_types": ["text"],
            "steps": [{"uses": old_light.ref}],
        }
    )
    version = MethodVersion.objects.create(
        method=method, number=1, definition=definition, tests=old_light.tests, created_by=user
    )
    version.used_versions.set([old_light])
    return method


@pytest.fixture
def question(course: Course) -> Question:
    assessment = Assessment.objects.create(course=course, name="Assessment")
    return Question.objects.create(assessment=assessment, key="Q1", position=1, definition={"type": "text"})


def marking_of(question: Question, uses: str, **settings: object) -> Marking:
    definition = {"question": "Q1", "uses": uses} | ({"with": settings} if settings else {})
    return Marking.objects.create(
        assessment=question.assessment, question=question, definition=dump_yaml(definition), questions_marked=["Q1"]
    )


def test_pages_load_with_versions_an_earlier_core_saved(
    client: Client, course: Course, light: Method, wrapper: Method, question: Question
) -> None:
    marking_of(question, MethodVersion.objects.get(method=wrapper).ref)
    assessment = question.assessment
    urls = [
        reverse("course", args=[course.pk]),
        reverse("course_methods", args=[course.pk]),
        reverse("your_methods"),
        reverse("overview", args=[assessment.pk]),
        reverse("question", args=[assessment.pk, question.pk]),
        reverse("marking", args=[assessment.pk, question.pk]),
        reverse("question_methods", args=[assessment.pk, question.pk]),
        reverse("marking_history", args=[assessment.pk, question.pk]),
        reverse("method_page", args=[light.pk, 1]),
        reverse("method_page", args=[light.pk, 2]),
        reverse("method_page", args=[wrapper.pk, 1]),
    ]
    statuses = {url: client.get(url).status_code for url in urls}
    assert statuses == dict.fromkeys(urls, 200)


def test_a_marking_using_a_version_an_earlier_core_saved_says_which_and_can_be_fixed(
    client: Client, light: Method, wrapper: Method, question: Question
) -> None:
    marking_of(question, MethodVersion.objects.get(method=wrapper).ref)
    assessment = question.assessment
    panel = client.get(reverse("marking", args=[assessment.pk, question.pk])).text
    assert "This marking isn't compatible with the current GradeFlow" in panel
    assert "It uses “Light” version 2, which has these problems:" in unescape(panel)
    assert bullets(panel) == OLD_PROBLEMS
    assert "Fix it by editing its YAML, or remove it." in panel
    editor = client.post(reverse("marking_start", args=[assessment.pk, question.pk]), headers=HTMX_HEADERS)
    assert editor.status_code == 200


def test_a_marking_an_earlier_core_saved_isnt_compatible(client: Client, light: Method, question: Question) -> None:
    marking_of(question, "gradeflow/key-points@1", points=["light"], evaluate_expected=False)
    panel = client.get(reverse("marking", args=[question.assessment.pk, question.pk])).text
    assert "This marking isn't compatible with the current GradeFlow" in panel
    assert "Fix it by editing its YAML, or remove it." in panel


def test_a_marking_whose_questions_changed_isnt_called_incompatible(client: Client, question: Question) -> None:
    marking_of(question, "gradeflow/number-range@1", min_value=1, max_value=2)
    panel = client.get(reverse("marking", args=[question.assessment.pk, question.pk])).text
    assert "This marking can't mark until it's fixed" in panel
    assert "YAML" not in panel


def test_versions_an_earlier_core_saved_say_so_and_the_rest_load(
    client: Client, course: Course, light: Method, wrapper: Method
) -> None:
    library = client.get(reverse("course_methods", args=[course.pk])).text
    badge = 'title="“Light” version 2 isn\'t compatible with the current GradeFlow">Not compatible'
    assert unescape(library).count(badge) == 2
    for method, number in [(light, 2), (wrapper, 1)]:
        page = client.get(reverse("method_page", args=[method.pk, number])).text
        assert "This version isn't compatible with the current GradeFlow" in unescape(page)
        assert ("It uses “Light” version 2, which has these problems:" in unescape(page)) == (method == wrapper)
        assert bullets(page) == OLD_PROBLEMS
    assert "compatible" not in client.get(reverse("method_page", args=[light.pk, 1])).text
    loaded = loaded_usable_methods(course.members.get().user, course)
    loaded.methods.find(MethodVersion.objects.get(method=light, number=1).ref)


def test_versions_an_earlier_core_saved_are_logged_once(
    client: Client, course: Course, light: Method, wrapper: Method, caplog: pytest.LogCaptureFixture
) -> None:
    load_saved.cache_clear()
    _log_failure.cache_clear()
    with caplog.at_level(logging.WARNING, "gradeflow_app.libraries.collection"):
        for _ in range(2):
            client.get(reverse("course_methods", args=[course.pk]))
    problems = " ".join(OLD_PROBLEMS)
    assert sorted(record.getMessage() for record in caplog.records) == [
        f"Saved method method-laboratory/light@2 doesn't load, for problems in method-laboratory/light@2: {problems}",
        f"Saved method method-laboratory/wrapper@1 doesn't load, for problems in method-laboratory/light@2: {problems}",
    ]


def test_a_check_lists_what_this_core_cant_load(light: Method, wrapper: Method, question: Question, user: User) -> None:
    marking_of(question, MethodVersion.objects.get(method=wrapper).ref)
    old_draft = {
        "question": "Q1",
        "uses": "gradeflow/key-points@1",
        "with": {"points": ["a"], "evaluate_expected": 1, "variables": {}},
    }
    MarkingDraftRecord.objects.create(
        assessment=question.assessment, user=user, question=question, definition=dump_yaml(old_draft)
    )
    output = StringIO()
    call_command("check_saved", stdout=output)
    assert output.getvalue().splitlines() == [
        f"method-laboratory/light@2: {' '.join(OLD_PROBLEMS)}",
        "method-laboratory/wrapper@1: uses method-laboratory/light@2",
        "Method laboratory › Assessment › Q1: uses: There's no method method-laboratory/wrapper@1.",
        "Method laboratory › Assessment › Q1, draft by Ana Tan: evaluate_expected: Unknown setting. "
        "variables: Unknown setting.",
    ]
