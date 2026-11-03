import json
import re
from typing import Any

import pytest
from django.test import Client
from django.urls import reverse
from pytest_django import Settings

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Role
from gradeflow_app.publish.models import AssessmentCanvas, CourseCanvas, SentMarks
from gradeflow_app.results.models import Adjustment
from tests.conftest import HTMX_HEADERS, create_assessment, encrypted_id, member_client, upload

pytestmark = pytest.mark.django_db


PROXY_URL = "https://proxy.example.edu"


@pytest.fixture
def canvas_assessment(client: Client, assessment: Assessment, settings: Settings) -> Assessment:
    """`assessment`, in a course connected to a Canvas course."""
    settings.GRADEFLOW_CORS_PROXY_URL = PROXY_URL
    client.post(
        reverse("canvas_settings", args=[assessment.course_id]),
        {
            "canvas_course": "101",
            "canvas_course_name": "BIO1010 (2026 S2)",
            "canvas_assignment_group": "7",
            "canvas_assignment_group_name": "Exams",
            "marks_mode": "percent",
            "rounding": "0.5",
            "include_feedback": "on",
        },
    )
    return assessment


@pytest.fixture
def viewer(canvas_assessment: Assessment) -> Client:
    return member_client(canvas_assessment, Role.VIEWER)


def rows_to_send(page: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        page.split('<script id="publish-rows-data" type="application/json">')[1].split("</script>")[0]
    )
    return rows


def test_publishing_to_canvas_needs_a_proxy_and_a_canvas_course(client: Client, settings: Settings) -> None:
    assessment = create_assessment(client)
    publish = reverse("publish", args=[assessment.pk])
    assert b"Nothing to publish yet" in client.get(publish).content
    upload(client, assessment)
    settings.GRADEFLOW_CORS_PROXY_URL = ""
    assert b"set up on this server" in client.get(publish).content
    settings.GRADEFLOW_CORS_PROXY_URL = PROXY_URL
    assert b"canvasPublisher" not in client.get(publish).content
    response = client.post(
        reverse("canvas_settings", args=[assessment.course_id]),
        {"canvas_course": "101", "canvas_course_name": "BIO1010 (2026 S2)", "marks_mode": "percent", "rounding": "0.5"},
    )
    assert response.status_code == 302
    page = client.get(publish).content
    assert b"canvasPublisher" in page
    assert b'<span class="truncate font-medium">BIO1010 (2026 S2)</span>' in page, "the Canvas course it sends to"


def test_this_device_connects_to_canvas_among_its_integrations(
    client: Client, canvas_assessment: Assessment, settings: Settings
) -> None:
    for name, pk in (("publish", canvas_assessment.pk), ("canvas_settings", canvas_assessment.course_id)):
        page = client.get(reverse(name, args=[pk])).content
        assert b"Access token" not in page, f"{name} leaves connecting to Integrations"
        assert f'href="{reverse("integrations")}"'.encode() in page
    assert b"Access token" in client.get(reverse("integrations")).content
    settings.GRADEFLOW_CORS_PROXY_URL = ""
    assert b"Access token" not in client.get(reverse("integrations")).content, "Canvas isn't set up on this server"


def test_editors_get_the_rows_to_send_and_the_sending_form(client: Client, canvas_assessment: Assessment) -> None:
    page = client.get(reverse("publish", args=[canvas_assessment.pk])).content.decode()
    rows = rows_to_send(page)
    assert len(rows) == 10
    assert {"studentId", "marks", "maxMarks", "comment"} == rows[0].keys()
    assert encrypted_id("S001") in {row["studentId"] for row in rows}
    assert 'hx-post="{}"'.format(reverse("publish_sending", args=[canvas_assessment.pk])) in page
    assert 'name="marks_mode"' in page
    assert "As the course (percentage)" in page
    assert (
        'id="send-settings" class="min-w-0 flex-1" data-mode="percent" data-rounding="0.5" data-feedback="true"' in page
    )
    assert "Percentages, rounded to 0.5, with feedback" in page
    assert "Preview comment" in page
    assert 'id="send-review"' in page


def test_viewers_see_how_marks_are_sent_but_not_the_rows_or_controls(
    viewer: Client, canvas_assessment: Assessment
) -> None:
    page = viewer.get(reverse("publish", args=[canvas_assessment.pk])).content.decode()
    assert "Percentages, rounded to 0.5, with feedback" in page
    assert "Editors send the marks." in page
    assert "publish-rows" not in page
    assert encrypted_id("S001") not in page
    assert "canvasPublisher" not in page
    assert 'name="marks_mode"' not in page
    assert "assignment-picker" not in page


def test_viewers_cant_change_how_marks_are_sent_or_get_the_rows(viewer: Client, canvas_assessment: Assessment) -> None:
    response = viewer.post(reverse("publish_sending", args=[canvas_assessment.pk]), {"marks_mode": "marks"})
    assert response.status_code == 403
    assert viewer.get(reverse("publish_rows", args=[canvas_assessment.pk])).status_code == 403
    assert (
        viewer.post(reverse("publish_record", args=[canvas_assessment.pk]), {"student_count": "1"}).status_code == 403
    )


def test_the_rows_refresh_when_marks_change(client: Client, assessment: Assessment) -> None:
    rows = client.get(reverse("publish_rows", args=[assessment.pk]), headers=HTMX_HEADERS).content.decode()
    assert rows.startswith('<div id="publish-rows"')
    assert 'hx-trigger="live-' in rows
    assert encrypted_id("S001") in rows


def test_overriding_how_marks_are_sent_answers_with_the_summary(client: Client, canvas_assessment: Assessment) -> None:
    url = reverse("publish_sending", args=[canvas_assessment.pk])
    unchanged = client.post(url, {"assignment": "12", "marks_mode": "", "rounding": "", "include_feedback": "unknown"})
    assert 'data-mode="percent" data-rounding="0.5" data-feedback="true"' in unchanged.content.decode()
    assert "Custom" not in unchanged.content.decode()

    changed = client.post(
        url, {"assignment": "12", "marks_mode": "marks", "rounding": "0", "include_feedback": "false"}
    ).content.decode()
    assert 'data-mode="marks" data-rounding="0.0" data-feedback="false"' in changed
    assert "Marks, not rounded, without feedback" in changed
    assert re.search(r'<button type="button" id="comment-preview" [^>]* hx-swap-oob="true" hidden>', changed)
    assert "Custom" in changed
    assessment_canvas = AssessmentCanvas.objects.get(assessment=canvas_assessment)
    assert (assessment_canvas.canvas_assignment, assessment_canvas.marks_mode, assessment_canvas.include_feedback) == (
        "12",
        "marks",
        False,
    )
    assert CourseCanvas.objects.get(course=canvas_assessment.course).canvas_assignment_group == "7"

    refused = client.post(url, {"rounding": "-1"}, headers=HTMX_HEADERS)
    assert refused.status_code == 422
    assert {"level": "error", "text": "Round to 0 or more."} in json.loads(refused.headers["X-Messages"])


def test_a_send_is_recorded_with_its_assignment_and_count(
    client: Client, user: User, canvas_assessment: Assessment
) -> None:
    url = reverse("publish_record", args=[canvas_assessment.pk])
    response = client.post(url, {"assignment_name": "Midterm", "student_count": "9"})
    assert response.status_code == 200
    assert b"Midterm" in response.content
    assert b'<td class="tabular-nums text-right">9</td>' in response.content
    publication = SentMarks.objects.get()
    assert (publication.assignment_name, publication.student_count, publication.sent_by) == ("Midterm", 9, user)
    activity = client.get(reverse("activity", args=[canvas_assessment.pk]), headers=HTMX_HEADERS).content.decode()
    assert "sent marks to “Midterm” (9 students)" in activity


@pytest.mark.parametrize(
    "fields",
    [
        {"assignment_name": "Midterm", "student_count": "many"},
        {"assignment_name": "Midterm"},
        {"assignment_name": "x" * 256, "student_count": "9"},
    ],
)
def test_a_malformed_send_is_refused(client: Client, assessment: Assessment, fields: dict[str, str]) -> None:
    response = client.post(reverse("publish_record", args=[assessment.pk]), fields)
    assert response.status_code == 400
    assert not SentMarks.objects.exists()


def test_each_comment_gives_the_marks_and_feedback_of_each_marked_question(
    client: Client, canvas_assessment: Assessment
) -> None:
    student = canvas_assessment.students.get(encrypted_id=encrypted_id("S001"))
    Adjustment.objects.create(
        student=student, question=canvas_assessment.questions.get(key="Q1"), feedback="Show working."
    )
    page = client.get(reverse("publish", args=[canvas_assessment.pk])).content.decode()
    row = next(row for row in rows_to_send(page) if row["studentId"] == encrypted_id("S001"))
    assert row["comment"] == EXPECTED_COMMENT


EXPECTED_COMMENT = "<strong>Q1 [1/1]</strong>\nShow working."


def test_the_comment_preview_pages_through_the_students(client: Client, canvas_assessment: Assessment) -> None:
    Adjustment.objects.create(
        student=canvas_assessment.students.get(encrypted_id=encrypted_id("S001")),
        question=canvas_assessment.questions.get(key="Q1"),
        feedback="Show working.",
    )
    url = reverse("publish_comment", args=[canvas_assessment.pk])
    first = client.get(url, headers=HTMX_HEADERS).content.decode()
    assert encrypted_id("S001") in first
    assert EXPECTED_COMMENT in first
    assert "1 of 10" in first
    assert 'aria-label="Previous student" disabled' in first
    last = client.get(f"{url}?page=99", headers=HTMX_HEADERS).content.decode()
    assert "10 of 10" in last
    assert 'aria-label="Next student" disabled' in last


def test_the_comment_preview_says_when_nothing_is_marked(client: Client, canvas_assessment: Assessment) -> None:
    upload(client, canvas_assessment, marks_columns="{}")
    preview = client.get(reverse("publish_comment", args=[canvas_assessment.pk]), headers=HTMX_HEADERS).content.decode()
    assert "Nothing marked yet." in preview
    assert "bg-base-200" not in preview


def test_viewers_may_preview_comments(viewer: Client, canvas_assessment: Assessment) -> None:
    assert viewer.get(reverse("publish_comment", args=[canvas_assessment.pk]), headers=HTMX_HEADERS).status_code == 200


@pytest.mark.parametrize("page", ["canvas_settings", "publish"])
def test_only_the_group_picker_names_a_new_group(client: Client, canvas_assessment: Assessment, page: str) -> None:
    pk = canvas_assessment.course_id if page == "canvas_settings" else canvas_assessment.pk
    rendered = client.get(reverse(page, args=[pk])).content.decode()
    group_picker = rendered.split('<dialog id="group-picker"')[1].split("</dialog>")[0]
    assert 'id="new-group-name"' in group_picker
    assert rendered.count('id="new-group-name"') == 1


def test_a_course_refuses_a_negative_rounding_step(client: Client, canvas_assessment: Assessment) -> None:
    page = client.post(
        reverse("canvas_settings", args=[canvas_assessment.course_id]), {"marks_mode": "marks", "rounding": "-1"}
    )
    assert page.status_code == 200
    assert "Round to 0 or more." in page.content.decode()
    assert '<details class="group/more" open' in page.content.decode()
    assert CourseCanvas.objects.get(course=canvas_assessment.course).rounding == 0.5
