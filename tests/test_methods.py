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
from gradeflow_app.libraries.models import Method, MethodDraft
from gradeflow_app.marking.checks import tree_of
from gradeflow_app.marking.models import Draft, Marking
from gradeflow_app.questions.models import TYPE_LABELS
from gradeflow_app.results.models import Result
from tests.conftest import HTMX, course_of, definition, edit, light, member, publish, published, start_marking

pytestmark = pytest.mark.django_db


def saved_marking(
    client: Client, assessment: Assessment, captured: DjangoCaptureOnCommitCallbacks, points: list[str]
) -> Marking:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    client.post(url, {"path": "", "fields": "1", "points": points, "combine": "any"})
    with captured(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    return Marking.objects.get(question__key="Q1")


def save_as_method(client: Client, assessment: Assessment, **posted: str) -> int:
    """Save Q1's marking as a method, in the course's library."""
    question = assessment.questions.get(key="Q1")
    return client.post(reverse("method_save", args=[assessment.pk, question.pk]), posted, headers=HTMX).status_code


def save_method(client: Client, assessment: Assessment, label: str, *, fill_in: bool = False) -> Method:
    """A method published from the saved marking of Q1, called `label`; with `fill_in`, its points an input."""
    assert save_as_method(client, assessment, label=label, summary="Full marks for the word.") == 200
    draft = MethodDraft.objects.get(method__library__course=assessment.course, method__versions=None)
    if fill_in:
        assert (
            edit(client, draft.method, "method_field", op="new", field="with.checks.0.with.points").status_code == 200
        )
    assert "HX-Redirect" in publish(client, draft.method).headers
    return draft.method


def publish_edit(client: Client, method: Method, definition: str) -> bool:
    """Edit the latest version as `definition` and publish it: whether it was published."""
    assert client.post(reverse("method_edit", args=[method.pk])).status_code == 302
    assert edit(client, method, "method_yaml", definition=definition).status_code == 200
    return "HX-Redirect" in publish(client, method).headers


def test_a_marking_saved_as_a_method_marks_like_it_with_what_others_fill_in(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    question = assessment.questions.get(key="Q1")
    sheet = client.get(reverse("method_save", args=[assessment.pk, question.pk])).content.decode()
    assert "Save as a method" in sheet
    saved = save_method(client, assessment, "Key word", fill_in=True)
    assert saved.name == "key-word"
    version = saved.versions.get()

    picker = client.get(reverse("question_methods", args=[assessment.pk, question.pk])).content.decode()
    assert "BIO1010</p>" in picker, "the course's own methods under its name"
    assert "Key word" in picker
    Marking.objects.all().delete()
    url, draft = start_marking(client, assessment, "Q1", version.ref)
    assert tree_of(draft.definition)["uses"] == "bio1010/key-word@1"
    client.post(url, {"path": "", "fields": "1", "points": ["chlorophyll"]})
    assert tree_of(Draft.objects.get().definition)["with"] == {"points": ["chlorophyll"]}, "filled in"
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    assert [str(used) for used in Marking.objects.get(question=question).uses.all()] == [version.ref]
    assert Result.objects.filter(question=question).count() == assessment.students.count()


def test_a_method_needs_a_name_and_a_summary(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    assert save_as_method(client, assessment, label="", summary="") == 422
    assert not Method.objects.exists()


def test_a_method_in_use_says_what_uses_it_and_isnt_deleted(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    saved = save_method(client, assessment, "Key word")
    Marking.objects.all().delete()
    _, draft = start_marking(client, assessment, "Q1", "bio1010/key-word@1")
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    listed = client.get(reverse("course_methods", args=[assessment.course_id])).content.decode()
    assert "Key word" in listed
    assert "1 marking</td>" in listed
    response = client.post(reverse("method_delete", args=[saved.pk]))
    assert [message.message for message in get_messages(response.wsgi_request)] == ["Still used by Midterm · Q1."]
    assert Method.objects.filter(pk=saved.pk).exists()


def test_viewers_cant_save_edit_or_delete_methods(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    saved = save_method(client, assessment, "Key word")
    viewer = member(assessment, Role.VIEWER)
    assert save_as_method(viewer, assessment, label="Mine", summary="Mine.") == 403
    course = assessment.course_id
    for name in ("method_delete", "method_archive", "method_share", "method_edit"):
        assert viewer.post(reverse(name, args=[saved.pk])).status_code == 403, name
    assert viewer.post(f"{reverse('method_new')}?course={course}", {"label": "Mine"}).status_code == 403
    assert "New method" not in viewer.get(reverse("course_methods", args=[course])).content.decode()
    assert viewer.get(reverse("course_methods", args=[course])).status_code == 200
    assert not MethodDraft.objects.exists()
    assert not saved.archived


def test_publishing_an_edit_leaves_markings_on_the_version_they_use(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    method = save_method(client, assessment, "Key word")
    Marking.objects.all().delete()
    _, draft = start_marking(client, assessment, "Q1", "bio1010/key-word@1")
    with django_capture_on_commit_callbacks(execute=True):
        client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    question = assessment.questions.get(key="Q1")
    results = set(Result.objects.filter(question=question).values_list("student", "proportion"))
    first = method.versions.get().definition
    broken = first.replace("uses: gradeflow/key-points@1", "uses: gradeflow/nope@1")
    assert not publish_edit(client, method, broken)
    assert "gradeflow/nope@1" in method.drafts.get().definition
    assert method.versions.count() == 1
    client.post(reverse("method_discard", args=[method.pk]))
    with django_capture_on_commit_callbacks(execute=True):
        assert publish_edit(client, method, first.replace("- light", "- chlorophyll"))
    assert list(method.versions.values_list("number", flat=True)) == [2, 1]
    assert [str(version) for version in Marking.objects.get(question=question).uses.all()] == ["bio1010/key-word@1"]
    assert set(Result.objects.filter(question=question).values_list("student", "proportion")) == results


def test_a_method_isnt_saved_under_a_name_its_library_has(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    save_method(client, assessment, "Key word")
    question = assessment.questions.get(key="Q1")
    again = client.post(reverse("method_save", args=[assessment.pk, question.pk]), {"label": "key word"}, headers=HTMX)
    assert again.status_code == 422
    assert "This library already has a method called “Key word”." in again.content.decode(), "with no summary yet"
    assert Method.objects.count() == 1
    other = save_method(client, assessment, "Other word")
    renamed = other.versions.get().definition.replace("label: Other word", "label: Key word")
    assert not publish_edit(client, other, renamed)
    assert other.versions.count() == 1
    assert other.drafts.exists()


def test_the_methods_page_says_what_each_method_marks(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    save_method(client, assessment, "Key word")
    listed = client.get(reverse("course_methods", args=[assessment.course_id])).content.decode()
    text = TYPE_LABELS["text"]
    assert f"{text}</td>" in listed
    assert "Not used" in listed
    assert "Members</td>" in listed
    assert 'tabular-nums text-right">1</td>' in listed


def test_the_marking_panel_lists_the_courses_methods_on_their_own(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    save_method(client, assessment, "Key word")
    question = assessment.questions.get(key="Q1")
    picker = client.get(reverse("question_methods", args=[assessment.pk, question.pk])).content.decode()
    assert picker.index("BIO1010</p>") < picker.index("Key word")


def test_the_picker_has_built_in_yours_and_public_tabs(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    question = assessment.questions.get(key="Q1")
    url = reverse("question_methods", args=[assessment.pk, question.pk])
    empty = client.get(url).content.decode()
    assert 'data-tab="yours"' in empty, "the tabs stay when nothing fits"
    assert re.search(r"Public</button>", empty), "an empty tab shows no count"
    assert "None for this question" in empty
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    version = save_method(client, assessment, "Key word").versions.get()
    bo = User.objects.create_user("bo@example.edu", "another-password", name="Bo Lim")
    shared = light(bo.library, bo, label="Bright")
    Method.objects.filter(pk=shared.method_id).update(public=True)
    picker = client.get(url).content.decode()
    assert 'data-tab="builtin" aria-selected="true"' in picker
    assert 'data-tab="yours" aria-selected="false"' in picker
    assert re.search(r"Yours<span[^>]*>1</span>", picker)
    assert re.search(r"Public<span[^>]*>1</span>", picker)
    assert '<div data-section data-tab="yours" hidden>' in picker
    assert '<div data-section data-tab="public" hidden>' in picker
    assert "by Bo Lim" in picker
    chosen = client.get(url, {"selected": version.ref}).content.decode()
    assert 'data-tab="yours" aria-selected="true"' in chosen, "opens where the chosen method is"
    assert '<div data-section data-tab="builtin" hidden>' in chosen


def test_a_saved_marking_opens_as_an_unpublished_draft_only_its_author_sees(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    saved_marking(client, assessment, django_capture_on_commit_callbacks, ["light"])
    question = assessment.questions.get(key="Q1")
    response = client.post(
        reverse("method_save", args=[assessment.pk, question.pk]),
        {"label": "Draft keyword", "summary": "Reusable."},
        headers=HTMX,
    )
    draft = MethodDraft.objects.get(method__library__course=assessment.course)
    assert response.headers["HX-Redirect"] == reverse("method_page", args=[draft.method_id])
    assert list(draft.tests["questions"]) == ["Q1"]
    assert draft.tests["rows"] == []
    assert not draft.method.versions.exists()
    listing = reverse("course_methods", args=[assessment.course_id])
    assert b"Draft keyword" in client.get(listing).content
    assert b"Draft keyword" not in client.get(reverse("question_methods", args=[assessment.pk, question.pk])).content
    viewer = member(assessment, Role.VIEWER)
    assert b"Draft keyword" not in viewer.get(listing).content
    assert viewer.get(response.headers["HX-Redirect"], follow=True).status_code == 404


def test_a_question_groups_marking_saves_as_a_method_for_its_questions(client: Client, assessment: Assessment) -> None:
    marking = Marking.objects.create(
        assessment=assessment,
        definition=dump_yaml(
            {
                "uses": "gradeflow/combination@1",
                "with": {
                    "checks": [
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
    draft = MethodDraft.objects.get(method__library__course=assessment.course)
    assert set(draft.tests["questions"]) == {"Q1", "Q2"}
    assert client.get(response.headers["Location"]).status_code == 200
    assert not draft.method.versions.exists()
    assert not Draft.objects.exists()


def test_the_methods_pages_list_a_librarys_methods_and_what_a_course_uses_from_elsewhere(
    client: Client, assessment: Assessment, user: User
) -> None:
    bo = User.objects.create_user("bo@example.edu", "another-password", name="Bo Lim")
    version = light(bo.library, bo, label="Bright")
    question = assessment.questions.get(key="Q1")
    Marking.objects.create(
        assessment=assessment, question=question, definition=dump_yaml({"question": "Q1", "uses": version.ref})
    )
    published(version.method, bo, definition("Bright", answer="sun"))
    Method.objects.filter(pk=version.method_id).update(public=True)
    listed = client.get(reverse("course_methods", args=[assessment.course_id])).content.decode()
    assert "Used here, from elsewhere" in listed
    assert "by Bo Lim" in listed
    assert "1 marking</td>" in listed
    assert "2 is out" in listed
    assert "No methods yet" in client.get(reverse("your_methods")).content.decode()
    light(user.library, user, label="Mine")
    yours = client.get(reverse("your_methods")).content.decode()
    assert "Mine" in yours
    assert "Only you</td>" in yours
    assert "Bright" not in yours


def test_your_methods_are_a_page_of_their_own_not_the_accounts(client: Client, user: User) -> None:
    mine = light(user.library, user, label="Mine")
    bo = User.objects.create_user("bo@example.edu", "another-password", name="Bo Lim")
    theirs = light(bo.library, bo, label="Bright")
    Method.objects.filter(pk=theirs.method_id).update(public=True)
    listed = client.get(reverse("your_methods")).content.decode()
    assert "Signed-in devices" not in listed
    crumb = f'<a href="{reverse("your_methods")}">Your methods</a>'
    assert crumb in client.get(reverse("method_page", args=[mine.method_id, mine.number])).content.decode()
    assert crumb not in client.get(reverse("method_page", args=[theirs.method_id, theirs.number])).content.decode()


def test_a_method_counts_the_markings_that_use_any_of_its_versions(
    client: Client, assessment: Assessment, user: User
) -> None:
    bo = User.objects.create_user("bo@example.edu", "another-password", name="Bo Lim")
    first = light(bo.library, bo, label="Bright")
    second = published(first.method, bo, definition("Bright", answer="sun"))
    mine = light(user.library, user, label="Mine")
    quiz = Assessment.objects.create(course=course_of(bo), name="Quiz")
    for version, where in [(first, assessment), (second, assessment), (second, quiz), *[(mine, assessment)] * 3]:
        Marking.objects.create(assessment=where, definition=dump_yaml({"uses": version.ref}))
    listed = client.get(reverse("course_methods", args=[assessment.course_id])).content.decode()
    assert "2 markings</td>" in listed, "Bright, by this course's markings alone"
    assert "3 markings</td>" in listed
    assert "3 markings</td>" in client.get(reverse("your_methods")).content.decode(), "Mine, in the person's courses"
