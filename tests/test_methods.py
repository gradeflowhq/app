import re

import pytest
from django.contrib.messages import get_messages
from django.test import Client
from django.urls import reverse
from gradeflow_core.editing import dump_yaml
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Role
from gradeflow_app.libraries.models import Method, MethodDraftRecord
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.questions.models import QUESTION_TYPE_LABELS
from gradeflow_app.results.models import Result
from tests.conftest import (
    HTMX_HEADERS,
    create_course,
    member_client,
    method_definition,
    post_publish,
    post_to_designer,
    publish_light_method,
    publish_version,
    save_draft,
    start_marking,
)

pytestmark = pytest.mark.django_db


def save_key_points_marking(client: Client, assessment: Assessment, points: list[str]) -> Marking:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    client.post(url, {"path": "", "fields_posted": "1", "points": points, "marks_by": "any_correct"})
    save_draft(client, draft)
    return Marking.objects.get(question__key="Q1")


def post_save_as_method(client: Client, assessment: Assessment, **fields: str) -> int:
    """Save Q1's marking as a method, in the course's library."""
    question = assessment.questions.get(key="Q1")
    return client.post(
        reverse("method_save", args=[assessment.pk, question.pk]), fields, headers=HTMX_HEADERS
    ).status_code


def publish_marking_as_method(
    client: Client, assessment: Assessment, label: str, *, with_points_setting: bool = False
) -> Method:
    """A method published from Q1's saved marking, called `label`. `with_points_setting` makes its points a setting."""
    assert post_save_as_method(client, assessment, label=label, summary="Full marks for the word.") == 200
    draft = MethodDraftRecord.objects.get(method__library__course=assessment.course, method__versions=None)
    if with_points_setting:
        assert (
            post_to_designer(
                client, draft.method, "method_field", operation="bind_new_setting", field="with.steps.0.with.points"
            ).status_code
            == 200
        )
    assert "HX-Redirect" in post_publish(client, draft.method).headers
    return draft.method


def edit_and_publish(client: Client, method: Method, definition: str) -> bool:
    """Edit the latest version as `definition` and publish it: whether it was published."""
    assert client.post(reverse("method_edit", args=[method.pk])).status_code == 302
    assert post_to_designer(client, method, "method_yaml", definition=definition).status_code == 200
    return "HX-Redirect" in post_publish(client, method).headers


def test_a_marking_saved_as_a_method_marks_like_it_with_what_others_fill_in(
    client: Client, assessment: Assessment
) -> None:
    save_key_points_marking(client, assessment, ["light"])
    question = assessment.questions.get(key="Q1")
    save_dialog = client.get(reverse("method_save", args=[assessment.pk, question.pk])).text
    assert "Save as a method" in save_dialog
    saved = publish_marking_as_method(client, assessment, "Key word", with_points_setting=True)
    assert saved.name == "key-word"
    version = saved.versions.get()

    picker = client.get(reverse("question_methods", args=[assessment.pk, question.pk])).text
    assert "BIO1010</p>" in picker, "the course's own methods under its name"
    assert "Key word" in picker
    Marking.objects.all().delete()
    url, draft = start_marking(client, assessment, "Q1", version.ref)
    assert definition_data(draft.definition)["uses"] == "bio1010/key-word@1"
    client.post(url, {"path": "", "fields_posted": "1", "points": ["chlorophyll"]})
    assert definition_data(MarkingDraftRecord.objects.get().definition)["with"] == {"points": ["chlorophyll"]}, (
        "filled in"
    )
    save_draft(client, draft)
    assert [str(used) for used in Marking.objects.get(question=question).used_versions.all()] == [version.ref]
    assert Result.objects.filter(question=question).count() == assessment.students.count()


def test_a_method_needs_a_name_and_a_summary(client: Client, assessment: Assessment) -> None:
    save_key_points_marking(client, assessment, ["light"])
    assert post_save_as_method(client, assessment, label="", summary="") == 422
    assert not Method.objects.exists()


def test_a_method_in_use_says_what_uses_it_and_isnt_deleted(client: Client, assessment: Assessment) -> None:
    save_key_points_marking(client, assessment, ["light"])
    saved = publish_marking_as_method(client, assessment, "Key word")
    Marking.objects.all().delete()
    _, draft = start_marking(client, assessment, "Q1", "bio1010/key-word@1")
    save_draft(client, draft)
    methods_page = client.get(reverse("course_methods", args=[assessment.course_id])).text
    assert "Key word" in methods_page
    assert "1 marking</td>" in methods_page
    response = client.post(reverse("method_delete", args=[saved.pk]))
    assert [message.message for message in get_messages(response.wsgi_request)] == ["Still used by Midterm · Q1."]
    assert Method.objects.filter(pk=saved.pk).exists()


def test_viewers_cant_save_edit_or_delete_methods(client: Client, assessment: Assessment) -> None:
    save_key_points_marking(client, assessment, ["light"])
    saved = publish_marking_as_method(client, assessment, "Key word")
    viewer = member_client(assessment, Role.VIEWER)
    assert post_save_as_method(viewer, assessment, label="Mine", summary="Mine.") == 403
    course_id = assessment.course_id
    for name in ("method_delete", "method_archive", "method_share", "method_edit"):
        assert viewer.post(reverse(name, args=[saved.pk])).status_code == 403, name
    assert viewer.post(reverse("method_new"), {"label": "Mine"}, query_params={"course": course_id}).status_code == 403
    assert "New method" not in viewer.get(reverse("course_methods", args=[course_id])).text
    assert viewer.get(reverse("course_methods", args=[course_id])).status_code == 200
    assert not MethodDraftRecord.objects.exists()
    assert not saved.archived


def test_publishing_an_edit_leaves_markings_on_the_version_they_use(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    save_key_points_marking(client, assessment, ["light"])
    method = publish_marking_as_method(client, assessment, "Key word")
    Marking.objects.all().delete()
    _, draft = start_marking(client, assessment, "Q1", "bio1010/key-word@1")
    save_draft(client, draft)
    question = assessment.questions.get(key="Q1")
    results = set(Result.objects.filter(question=question).values_list("student", "proportion"))
    first = method.versions.get().definition
    broken = first.replace("uses: gradeflow/key-points@1", "uses: gradeflow/nope@1")
    assert not edit_and_publish(client, method, broken)
    assert "gradeflow/nope@1" in method.drafts.get().definition
    assert method.versions.count() == 1
    client.post(reverse("method_discard", args=[method.pk]))
    with django_capture_on_commit_callbacks(execute=True):
        assert edit_and_publish(client, method, first.replace("- light", "- chlorophyll"))
    assert list(method.versions.values_list("number", flat=True)) == [2, 1]
    assert [str(version) for version in Marking.objects.get(question=question).used_versions.all()] == [
        "bio1010/key-word@1"
    ]
    assert set(Result.objects.filter(question=question).values_list("student", "proportion")) == results


def test_a_method_isnt_saved_under_a_name_its_library_has(client: Client, assessment: Assessment) -> None:
    save_key_points_marking(client, assessment, ["light"])
    publish_marking_as_method(client, assessment, "Key word")
    question = assessment.questions.get(key="Q1")
    again = client.post(
        reverse("method_save", args=[assessment.pk, question.pk]), {"label": "key word"}, headers=HTMX_HEADERS
    )
    assert again.status_code == 422
    assert "This library already has a method called “Key word”." in again.text, "with no summary yet"
    assert Method.objects.count() == 1
    other = publish_marking_as_method(client, assessment, "Other word")
    renamed = other.versions.get().definition.replace("label: Other word", "label: Key word")
    assert not edit_and_publish(client, other, renamed)
    assert other.versions.count() == 1
    assert other.drafts.exists()


def test_the_methods_page_says_what_each_method_marks(client: Client, assessment: Assessment) -> None:
    save_key_points_marking(client, assessment, ["light"])
    publish_marking_as_method(client, assessment, "Key word")
    methods_page = client.get(reverse("course_methods", args=[assessment.course_id])).text
    text = QUESTION_TYPE_LABELS["text"]
    assert f"{text}</td>" in methods_page
    assert "Not used" in methods_page
    assert "Members</td>" in methods_page
    assert 'tabular-nums text-right">1</td>' in methods_page


def test_the_marking_panel_lists_the_courses_methods_on_their_own(client: Client, assessment: Assessment) -> None:
    save_key_points_marking(client, assessment, ["light"])
    publish_marking_as_method(client, assessment, "Key word")
    question = assessment.questions.get(key="Q1")
    picker = client.get(reverse("question_methods", args=[assessment.pk, question.pk])).text
    assert picker.index("BIO1010</p>") < picker.index("Key word")


def test_the_picker_has_built_in_yours_and_public_tabs(client: Client, assessment: Assessment, bo: User) -> None:
    question = assessment.questions.get(key="Q1")
    url = reverse("question_methods", args=[assessment.pk, question.pk])
    empty = client.get(url).text
    assert 'data-tab="yours"' in empty, "the tabs stay when nothing fits"
    assert re.search(r"Public</button>", empty), "an empty tab shows no count"
    assert "None for this question" in empty
    save_key_points_marking(client, assessment, ["light"])
    version = publish_marking_as_method(client, assessment, "Key word").versions.get()
    shared = publish_light_method(bo.library, bo, label="Bright")
    Method.objects.filter(pk=shared.method_id).update(public=True)
    picker = client.get(url).text
    assert 'data-tab="builtin" aria-selected="true"' in picker
    assert 'data-tab="yours" aria-selected="false"' in picker
    assert re.search(r"Yours<span[^>]*>1</span>", picker)
    assert re.search(r"Public<span[^>]*>1</span>", picker)
    assert '<div data-section data-tab="yours" hidden>' in picker
    assert '<div data-section data-tab="public" hidden>' in picker
    assert "by Bo Lim" in picker
    chosen = client.get(url, {"selected": version.ref}).text
    assert 'data-tab="yours" aria-selected="true"' in chosen, "opens where the chosen method is"
    assert '<div data-section data-tab="builtin" hidden>' in chosen


def test_a_saved_marking_opens_as_an_unpublished_draft_only_its_author_sees(
    client: Client, assessment: Assessment
) -> None:
    save_key_points_marking(client, assessment, ["light"])
    question = assessment.questions.get(key="Q1")
    response = client.post(
        reverse("method_save", args=[assessment.pk, question.pk]),
        {"label": "Draft keyword", "summary": "Reusable."},
        headers=HTMX_HEADERS,
    )
    draft = MethodDraftRecord.objects.get(method__library__course=assessment.course)
    assert response.headers["HX-Redirect"] == reverse("method_page", args=[draft.method_id])
    assert list(draft.tests["questions"]) == ["Q1"]
    assert draft.tests["rows"] == []
    assert not draft.method.versions.exists()
    listing = reverse("course_methods", args=[assessment.course_id])
    assert b"Draft keyword" in client.get(listing).content
    assert b"Draft keyword" not in client.get(reverse("question_methods", args=[assessment.pk, question.pk])).content
    viewer = member_client(assessment, Role.VIEWER)
    assert b"Draft keyword" not in viewer.get(listing).content
    assert viewer.get(response.headers["HX-Redirect"], follow=True).status_code == 404


def test_a_question_groups_marking_saves_as_a_method_for_its_questions(client: Client, assessment: Assessment) -> None:
    marking = Marking.objects.create(
        assessment=assessment,
        definition=dump_yaml(
            {
                "uses": "gradeflow/workflow@1",
                "with": {
                    "steps": [
                        {"question": "Q1", "uses": "gradeflow/full-marks@1"},
                        {"question": "Q2", "uses": "gradeflow/full-marks@1"},
                    ]
                },
            }
        ),
    )
    response = client.post(
        reverse("question_group_method_save", args=[assessment.pk, marking.pk]),
        {"label": "Two questions", "summary": "Full marks for two questions."},
    )
    assert response.status_code == 302
    draft = MethodDraftRecord.objects.get(method__library__course=assessment.course)
    assert set(draft.tests["questions"]) == {"Q1", "Q2"}
    assert client.get(response.headers["Location"]).status_code == 200
    assert not draft.method.versions.exists()
    assert not MarkingDraftRecord.objects.exists()


def test_the_methods_pages_list_a_librarys_methods_and_what_a_course_uses_from_elsewhere(
    client: Client, assessment: Assessment, user: User, bo: User
) -> None:
    version = publish_light_method(bo.library, bo, label="Bright")
    question = assessment.questions.get(key="Q1")
    Marking.objects.create(
        assessment=assessment, question=question, definition=dump_yaml({"question": "Q1", "uses": version.ref})
    )
    publish_version(version.method, bo, method_definition("Bright", answer="sun"))
    Method.objects.filter(pk=version.method_id).update(public=True)
    methods_page = client.get(reverse("course_methods", args=[assessment.course_id])).text
    assert "From other libraries" in methods_page
    assert "by Bo Lim" in methods_page
    assert "1 marking</td>" in methods_page
    assert "2 available" in methods_page
    assert "No methods yet" in client.get(reverse("your_methods")).text
    publish_light_method(user.library, user, label="Mine")
    yours = client.get(reverse("your_methods")).text
    assert "Mine" in yours
    assert "Only you</td>" in yours
    assert "Bright" not in yours


def test_your_methods_are_a_page_of_their_own_not_the_accounts(client: Client, user: User, bo: User) -> None:
    mine = publish_light_method(user.library, user, label="Mine")
    theirs = publish_light_method(bo.library, bo, label="Bright")
    Method.objects.filter(pk=theirs.method_id).update(public=True)
    methods_page = client.get(reverse("your_methods")).text
    assert "Signed-in devices" not in methods_page
    breadcrumb = f'<a href="{reverse("your_methods")}">Your methods</a>'
    assert breadcrumb in client.get(reverse("method_page", args=[mine.method_id, mine.number])).text
    assert breadcrumb not in client.get(reverse("method_page", args=[theirs.method_id, theirs.number])).text


def test_a_method_counts_the_markings_that_use_any_of_its_versions(
    client: Client, assessment: Assessment, user: User, bo: User
) -> None:
    first = publish_light_method(bo.library, bo, label="Bright")
    second = publish_version(first.method, bo, method_definition("Bright", answer="sun"))
    mine = publish_light_method(user.library, user, label="Mine")
    quiz = Assessment.objects.create(course=create_course(bo), name="Quiz")
    for version, where in [(first, assessment), (second, assessment), (second, quiz), *[(mine, assessment)] * 3]:
        Marking.objects.create(assessment=where, definition=dump_yaml({"uses": version.ref}))
    methods_page = client.get(reverse("course_methods", args=[assessment.course_id])).text
    assert "2 markings</td>" in methods_page, "Bright, by this course's markings alone"
    assert "3 markings</td>" in methods_page
    assert "3 markings</td>" in client.get(reverse("your_methods")).text, "Mine, in the person's courses"
