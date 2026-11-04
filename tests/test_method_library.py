"""Libraries of saved methods: every published version is a method of its own, markings keep the versions they name,
and who may use, change, copy or delete a method."""

import re

import pytest
from allauth.account.models import EmailAddress
from django.core.exceptions import ValidationError
from django.db.models import RestrictedError
from django.test import Client, TestCase
from django.urls import reverse
from gradeflow_core.editing import dump_yaml

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.jobs.models import Job
from gradeflow_app.libraries import services
from gradeflow_app.libraries.access import can_edit, openable_methods_filter
from gradeflow_app.libraries.collection import course_methods, files_needed_by, offers, usable
from gradeflow_app.libraries.designer.workspace import MethodWorkspace
from gradeflow_app.libraries.models import Library, Method, MethodDraftRecord, MethodVersion, VersionUse
from gradeflow_app.marking.checks import definition_data
from gradeflow_app.marking.models import Marking, MarkingDraftRecord
from gradeflow_app.marking.remarks import schedule_marking
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.results.models import Result
from tests.conftest import (
    HTMX_HEADERS,
    create_course,
    create_method_draft,
    method_definition,
    publish_light_method,
    publish_version,
    save_draft,
    signed_in_client,
    start_marking,
)

pytestmark = pytest.mark.django_db


def save_marking(assessment: Assessment, uses: str, user: User) -> Marking:
    marking = Marking.objects.create(
        assessment=assessment,
        question=assessment.questions.get(key="Q1"),
        definition=dump_yaml({"question": "Q1", "uses": uses}),
    )
    with TestCase.captureOnCommitCallbacks(execute=True):
        schedule_marking(marking, user)
    marking.refresh_from_db()
    return marking


def publish_parent(library: Library, user: User, uses: MethodVersion) -> MethodVersion:
    """The first version of a method named "parent" that uses `uses`."""
    method = Method.objects.create(library=library, name="parent")
    return publish_version(method, user, method_definition("Parent", uses=uses.ref))


def usable_refs(user: User, course: Course | None) -> set[str]:
    return {method.ref for method in usable(user, course)}


def used_refs(marking_or_version: Marking | MethodVersion) -> set[str]:
    return {version.ref for version in marking_or_version.used_versions.all()}


@pytest.fixture
def library(assessment: Assessment) -> Library:
    return assessment.course.library


def test_every_published_version_is_a_method_and_only_the_latest_is_offered(
    assessment: Assessment, library: Library, user: User
) -> None:
    course = assessment.course
    first = publish_light_method(library, user)
    second = publish_version(first.method, user, method_definition(answer="chlorophyll"))
    assert (first.number, second.number) == (1, 2)
    assert {"bio1010/light@1", "bio1010/light@2", "gradeflow/key-points@1"} <= usable_refs(user, course)
    methods = usable(user, course)
    old = methods.find("bio1010/light@1")
    newer = offers(user, course).newer_version(old, methods)
    assert newer is not None
    assert newer.ref == "bio1010/light@2"


def test_publishing_numbers_the_version_and_ends_the_draft(library: Library, user: User) -> None:
    method = Method.objects.create(library=library, name="light")
    draft = create_method_draft(method, user, method_definition())
    version = MethodWorkspace(method, draft).publish(user)
    assert version.ref == "bio1010/light@1"
    assert definition_data(version.definition)["version"] == 1
    assert version.tests == draft.tests
    method.refresh_from_db()
    assert method.next_version_number == 2
    assert not MethodDraftRecord.objects.filter(pk=draft.pk).exists()


def test_a_draft_of_an_older_version_isnt_published(library: Library, user: User, bo: User) -> None:
    version = publish_light_method(library, user)
    stale_draft = create_method_draft(version.method, bo, method_definition(answer="stale"))
    publish_version(version.method, user, method_definition(answer="newer"))
    with pytest.raises(ValidationError, match="A newer version was published"):
        MethodWorkspace(version.method, stale_draft).publish(bo)
    assert version.method.versions.count() == 2
    assert MethodDraftRecord.objects.filter(pk=stale_draft.pk).exists()


def test_two_methods_in_a_library_arent_called_the_same(library: Library, user: User) -> None:
    publish_light_method(library, user)
    method = Method.objects.create(library=library, name="bright")
    draft = create_method_draft(method, user, method_definition(label="light"))
    with pytest.raises(ValidationError, match="This library already has a method called “Light”"):
        MethodWorkspace(method, draft).publish(user)
    assert not method.versions.exists()
    draft.delete()
    assert services.taken_label(library, "LIGHT") == "Light"
    assert not services.taken_label(library, "Light", excluding=Method.objects.get(name="light"))
    assert not services.taken_label(user.library, "Light")


def test_a_method_not_yet_published_holds_its_name(library: Library, user: User) -> None:
    method = Method.objects.create(library=library, name="bright")
    create_method_draft(method, user, method_definition(label="Bright"))
    assert services.taken_label(library, "BRIGHT")
    assert not services.taken_label(library, "Bright", excluding=method)


def test_new_methods_get_a_name_free_in_the_library(library: Library) -> None:
    assert services.available_name("Key word!", library) == "key-word"
    Method.objects.create(library=library, name="key-word")
    assert services.available_name("Key word", library) == "key-word-2"
    assert services.available_name("???", library) == "method"


def test_people_and_courses_get_a_library_made_from_their_email_address_or_name(
    client: Client, user: User, bo: User
) -> None:
    assert (user.library.namespace, bo.library.namespace) == ("ana-example-edu", "bo-example-edu")
    assert User.objects.create_user("admin@example.edu", "a-long-password").library.namespace == ("admin-example-edu")
    client.post(reverse("courses"), {"name": "Ana Example EDU", "description": ""})
    client.post(reverse("courses"), {"name": "生物学", "description": ""})
    client.post(reverse("courses"), {"name": "GradeFlow", "description": ""})
    assert list(
        Library.objects.filter(course__isnull=False).order_by("created_at").values_list("namespace", flat=True)
    ) == [
        "ana-example-edu-2",
        "methods",
        "gradeflow-2",
    ]


def test_an_available_namespace_is_shortened_to_fit_its_number(user: User) -> None:
    long_name = " ".join(["word"] * 15)
    Library.objects.create(namespace=services.available_namespace(long_name))
    assert services.available_namespace(long_name) == "-".join(["word"] * 11) + "-2"
    assert services.available_namespace("ana@example.edu") == "ana-example-edu-2"
    assert services.available_namespace("ana@example.edu", excluding=user.library) == "ana-example-edu"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("ana@example.edu", "ana-example-edu"),
        ("Ana.Lim+marking@Example.EDU", "ana-lim-marking-example-edu"),
        ("  BIO1010 — Term 2!  ", "bio1010-term-2"),
        ("Biología Molecular", "biologia-molecular"),
        ("生物学 Biology", "biology"),
        ("生物学", ""),
        ("", ""),
    ],
)
def test_text_becomes_a_name(text: str, name: str) -> None:
    assert services.name_from(text, 60) == name


def test_a_long_name_is_shortened_at_a_hyphen() -> None:
    assert services.name_from("Introductory Biology for Majors", 25) == "introductory-biology-for"
    assert services.name_from("Introductory Biology", 19) == "introductory"
    assert services.name_from("a" * 70 + " b", 60) == "a" * 60


def test_a_marking_links_the_versions_it_needs_and_keeps_them_until_updated(
    client: Client,
    assessment: Assessment,
    library: Library,
    user: User,
) -> None:
    version = publish_light_method(library, user)
    parent = publish_parent(library, user, version)
    assert used_refs(parent) == {"bio1010/light@1"}
    assert VersionUse.objects.get(version=parent).used_version == version
    marking = save_marking(assessment, "bio1010/parent@1", user)
    assert used_refs(marking) == {"bio1010/parent@1", "bio1010/light@1"}, "with what it uses in turn"
    marking.delete()
    marking = save_marking(assessment, "bio1010/light@1", user)
    newer = publish_version(version.method, user, method_definition(answer="chlorophyll"))
    marking.refresh_from_db()
    assert used_refs(marking) == {"bio1010/light@1"}
    url, draft = start_marking(client, assessment, "Q1", "")
    assert (
        client.post(url, {"path": "", "operation": "use_latest", "target": ""}, headers=HTMX_HEADERS).status_code == 200
    )
    assert definition_data(MarkingDraftRecord.objects.get(pk=draft.pk).definition)["uses"] == "bio1010/light@2"
    save_draft(client, draft)
    marking.refresh_from_db()
    assert used_refs(marking) == {"bio1010/light@2"}
    remarked = marking.jobs.latest("created_at")
    assert remarked.marked_with["methods"] == [["bio1010/light", newer.definition]], "marked with the update"


def test_a_version_in_use_cant_be_deleted(
    assessment: Assessment,
    library: Library,
    user: User,
) -> None:
    version = publish_light_method(library, user)
    parent = publish_parent(library, user, version)
    with pytest.raises(RestrictedError):
        version.delete()
    parent.method.delete()
    save_marking(assessment, version.ref, user)
    with pytest.raises(RestrictedError):
        version.method.delete()


def test_a_marking_loads_only_the_versions_it_needs(library: Library, user: User) -> None:
    version = publish_light_method(library, user)
    publish_version(version.method, user, method_definition(answer="chlorophyll"))
    publish_parent(library, user, version)
    publish_light_method(library, user, name="unrelated", label="Unrelated")
    assert [name for name, _ in files_needed_by(["bio1010/parent@1"])] == ["bio1010/light", "bio1010/parent"]
    assert files_needed_by(["gradeflow/key-points@1"]) == ()


def test_a_course_marks_with_the_core_and_the_versions_its_markings_use(
    assessment: Assessment,
    library: Library,
    user: User,
) -> None:
    version = publish_light_method(library, user)
    publish_light_method(library, user, name="unused", label="Unused")
    refs_in_use = {method.ref for method in course_methods(assessment.course)}
    assert "gradeflow/key-points@1" in refs_in_use
    assert not {"bio1010/light@1", "bio1010/unused@1"} & refs_in_use
    save_marking(assessment, version.ref, user)
    refs_in_use = {method.ref for method in course_methods(assessment.course)}
    assert "bio1010/light@1" in refs_in_use
    assert "bio1010/unused@1" not in refs_in_use


def test_a_person_uses_their_own_their_courses_public_and_used_methods(
    assessment: Assessment,
    library: Library,
    user: User,
    bo: User,
) -> None:
    course = assessment.course
    publish_light_method(library, user, name="course", label="Course")
    publish_light_method(user.library, user, name="mine", label="Mine")
    theirs = bo.library
    private = publish_light_method(theirs, bo, name="private", label="Private")
    Method.objects.filter(pk=publish_light_method(theirs, bo, name="public", label="Public").method_id).update(
        public=True
    )
    assert usable_refs(user, None) >= {"ana-example-edu/mine@1", "bio1010/course@1", "bo-example-edu/public@1"}
    assert "bo-example-edu/private@1" not in usable_refs(user, course)
    assert "bio1010/course@1" not in usable_refs(bo, None)
    save_marking(assessment, private.ref, user)
    assert "bo-example-edu/private@1" in usable_refs(user, course), "a version the course uses"
    assert "bo-example-edu/private@1" not in usable_refs(user, None)


def test_the_picker_offers_saved_methods_by_where_they_come_from(
    assessment: Assessment,
    library: Library,
    user: User,
    bo: User,
) -> None:
    course = assessment.course
    publish_light_method(library, user, name="course", label="Course")
    publish_light_method(user.library, user, name="mine", label="Mine")
    chemistry = create_course(bo)
    Member.objects.create(course=chemistry, user=user, role=Role.VIEWER)
    publish_light_method(chemistry.library, bo, name="theirs", label="Theirs")
    bo_library = bo.library
    used = publish_light_method(bo_library, bo, name="used", label="Used")
    shared = publish_light_method(bo_library, bo, name="shared", label="Shared")
    Method.objects.filter(pk=shared.method_id).update(public=True)
    publish_light_method(bo_library, bo, name="hidden", label="Hidden")
    save_marking(assessment, used.ref, user)
    offered = offers(user, course)
    assert offered.sections == (
        ("Used in this course", ("bo-example-edu/used",)),
        ("BIO1010", ("bio1010/course",)),
        ("Your methods", ("ana-example-edu/mine",)),
        ("Chemistry", ("chemistry/theirs",)),
    )
    assert offered.public == ("bo-example-edu/shared",)
    assert offered.owners["bo-example-edu/used"] == "Bo Lim"
    assert offered.owners["chemistry/theirs"] == "Chemistry"
    assert offers(user, None).sections[0] == ("Your methods", ("ana-example-edu/mine",))


def test_archived_methods_are_offered_nowhere(
    assessment: Assessment,
    library: Library,
    user: User,
) -> None:
    course = assessment.course
    version = publish_light_method(library, user)
    publish_version(version.method, user, method_definition(answer="newer"))
    mine = publish_light_method(user.library, user, name="mine", label="Mine")
    save_marking(assessment, version.ref, user)
    Method.objects.filter(pk__in=[version.method_id, mine.method_id]).update(archived=True, public=True)
    offered = offers(user, course)
    assert offered.sections == ()
    methods = usable(user, course)
    first = methods.find(version.ref)
    assert offered.newer_version(first, methods) is None, "nor offered by Update"
    assert {version.ref, mine.ref} <= usable_refs(user, course), "what names it still works"


def test_who_opens_and_changes_a_method(
    assessment: Assessment,
    library: Library,
    user: User,
    bo: User,
) -> None:
    mine = publish_light_method(user.library, user).method
    course = publish_light_method(library, user, name="course", label="Course").method
    theirs = publish_light_method(bo.library, bo, name="theirs", label="Theirs").method

    def visible_method_names(person: User) -> set[str]:
        return {method.name for method in Method.objects.filter(openable_methods_filter(person)).distinct()}

    assert visible_method_names(user) == {"light", "course"}
    assert visible_method_names(bo) == {"theirs"}
    assert (can_edit(user, mine.library), can_edit(bo, mine.library)) == (True, False)
    Method.objects.filter(pk=mine.pk).update(public=True)
    assert visible_method_names(bo) == {"theirs", "light"}
    assert not can_edit(bo, mine.library), "public is for using, not changing"
    Member.objects.create(course=assessment.course, user=bo, role=Role.VIEWER)
    assert "course" in visible_method_names(bo)
    assert not can_edit(bo, course.library)
    Member.objects.filter(user=bo).update(role=Role.EDITOR)
    assert can_edit(bo, course.library)
    save_marking(assessment, theirs.versions.get().ref, user)
    assert "theirs" in visible_method_names(user), "a version the course uses"
    assert not can_edit(user, theirs.library)


def test_a_method_opens_for_those_who_can_see_it(client: Client, user: User, bo: User) -> None:
    method = publish_light_method(bo.library, bo).method
    page_url = reverse("method_latest", args=[method.pk])
    assert client.get(page_url).status_code == 404
    other = signed_in_client(bo)
    assert other.get(page_url).status_code == 302
    for name in ("method_archive", "method_share", "method_delete", "method_edit"):
        assert client.post(reverse(name, args=[method.pk])).status_code == 404, name
    other.post(reverse("method_share", args=[method.pk]), {"public": "1"})
    method.refresh_from_db()
    assert method.public
    assert client.get(page_url).status_code == 302
    for name in ("method_archive", "method_share", "method_delete", "method_edit"):
        assert client.post(reverse(name, args=[method.pk])).status_code == 403, name
    other.post(reverse("method_share", args=[method.pk]), {"public": "0"})
    method.refresh_from_db()
    assert not method.public


def test_an_archived_method_isnt_offered_but_its_markings_still_mark(
    client: Client,
    assessment: Assessment,
    library: Library,
    user: User,
) -> None:
    version = publish_light_method(library, user)
    response = client.post(reverse("method_archive", args=[version.method_id]))
    assert response.headers["Location"] == reverse("method_latest", args=[version.method_id])
    version.method.refresh_from_db()
    assert version.method.archived
    question = assessment.questions.get(key="Q1")
    picker = client.get(reverse("question_methods", args=[assessment.pk, question.pk])).text
    assert "Says light." not in picker
    marking = save_marking(assessment, version.ref, user)
    job = marking.jobs.get()
    assert job.status == Job.Status.COMPLETED
    assert Result.objects.filter(question=question).count() == assessment.students.count()
    draft = MarkingDraftRecord(assessment=assessment, question=question, definition=marking.definition, user=user)
    assert MarkingWorkspace(draft).valid
    client.post(reverse("method_archive", args=[version.method_id]), {"unarchive": "1"})
    assert "Says light." in client.get(reverse("question_methods", args=[assessment.pk, question.pk])).text


def test_a_method_in_use_isnt_deleted(
    client: Client,
    assessment: Assessment,
    library: Library,
    user: User,
    bo: User,
) -> None:
    version = publish_light_method(library, user)
    parent = publish_parent(library, user, version)
    save_marking(assessment, version.ref, user)
    other_course_quiz = Assessment.objects.create(course=create_course(bo), name="Quiz")
    Marking.objects.create(assessment=other_course_quiz, definition=dump_yaml({"uses": version.ref}))
    with pytest.raises(ValidationError) as refused:
        services.delete(version.method, user)
    assert refused.value.messages == ["Still used by Midterm · Q1, 1 other marking, Parent."]
    url = reverse("method_delete", args=[version.method_id])
    response = client.post(url)
    assert response.headers["Location"] == reverse("method_latest", args=[version.method_id])
    assert Method.objects.filter(pk=version.method_id).exists()
    Marking.objects.all().delete()
    parent.method.delete()
    response = client.post(url)
    assert response.headers["Location"] == reverse("course_methods", args=[assessment.course_id])
    assert not Method.objects.filter(pk=version.method_id).exists()


def test_deleting_a_course_deletes_its_unused_methods_and_keeps_the_rest_private(
    client: Client,
    assessment: Assessment,
    library: Library,
    user: User,
    bo: User,
) -> None:
    used = publish_light_method(library, user, name="used", label="Used")
    base = publish_light_method(library, user, name="base", label="Base")
    parent = publish_parent(library, user, base)
    unused = publish_light_method(library, user, name="unused", label="Unused")
    Method.objects.filter(library=library).update(public=True)
    other_course_quiz = Assessment.objects.create(course=create_course(bo), name="Quiz")
    Marking.objects.create(assessment=other_course_quiz, definition=dump_yaml({"uses": used.ref}))
    client.post(reverse("delete_course", args=[assessment.course_id]))
    assert not Course.objects.filter(pk=assessment.course_id).exists()
    library.refresh_from_db()
    assert library.course is None
    assert set(library.methods.values_list("name", "public")) == {("used", False)}
    assert not Method.objects.filter(pk__in=[base.method_id, parent.method_id, unused.method_id]).exists()
    assert services.available_namespace("BIO1010") == "bio1010-2", "the namespace stays taken"


def test_a_version_is_copied_as_a_new_method(library: Library, user: User) -> None:
    version = publish_version(Method.objects.create(library=library, name="light"), user, method_definition())
    second = publish_version(version.method, user, method_definition(answer="sun"))
    copied = services.copy_method(second, user.library, user, "My light")
    assert (copied.full_name, copied.next_version_number) == ("ana-example-edu/my-light", 2)
    [first] = copied.versions.all()
    assert (first.number, first.tests) == (1, second.tests)
    assert (definition_data(first.definition)["label"], definition_data(first.definition)["version"]) == ("My light", 1)
    assert "sun" in first.definition
    with pytest.raises(ValidationError, match="This library already has a method called “My light”"):
        services.copy_method(version, user.library, user, "my light")
    assert services.copy_method(version, user.library, user, "Light").full_name == "ana-example-edu/light"
    assert user.library.methods.count() == 2


def test_copy_offers_your_methods_then_the_courses_you_edit_newest_first(
    client: Client, library: Library, user: User, bo: User
) -> None:
    version = publish_light_method(library, user)
    older = create_course(user, "Chemistry")
    newer = create_course(user, "Physics")
    Member.objects.create(course=create_course(bo, "Botany"), user=user, role=Role.VIEWER)
    url = reverse("method_copy", args=[version.method_id])
    dialog = client.get(url, {"number": 1}).text
    assert "Copy Light to…" in dialog
    assert 'name="label" value="Light"' in dialog, "named as the method is"
    positions = [dialog.index(f'value="{item.pk}"') for item in (user.library, newer.library, older.library)]
    assert positions == sorted(positions)
    assert f'value="{library.pk}"' not in dialog, "not the method's own library"
    assert "Botany" not in dialog, "nor a course they only view"
    assert user.library.namespace not in dialog


@pytest.mark.parametrize("to_course", [False, True])
def test_copying_to_a_library_goes_to_the_copy(client: Client, library: Library, user: User, to_course: bool) -> None:
    version = publish_light_method(library, user)
    target = create_course(user).library if to_course else user.library
    url = reverse("method_copy", args=[version.method_id])
    fields = {"number": "1", "library": str(target.pk), "label": "Light"}
    response = client.post(url, fields, headers=HTMX_HEADERS)
    copied = Method.objects.get(library=target)
    assert response.headers["HX-Redirect"] == reverse("method_latest", args=[copied.pk])
    assert copied.full_name == f"{target.namespace}/light"
    again = client.post(url, fields, headers=HTMX_HEADERS)
    assert again.status_code == 422
    assert "This library already has a method called “Light”." in again.text
    assert f'value="{target.pk}" class="sr-only" checked' in again.text, "still chosen"
    assert client.post(url, fields | {"label": ""}, headers=HTMX_HEADERS).status_code == 422
    assert client.post(url, fields | {"library": str(library.pk)}, headers=HTMX_HEADERS).status_code == 422
    assert Method.objects.filter(library=target).count() == 1


def test_save_as_a_method_goes_to_the_markings_course(
    client: Client,
    assessment: Assessment,
    user: User,
) -> None:
    save_marking(assessment, "gradeflow/full-marks@1", user)
    question = assessment.questions.get(key="Q1")
    url = reverse("method_save", args=[assessment.pk, question.pk])
    form = client.get(url).text
    assert "Save to" not in form
    assert "bio1010" not in form
    response = client.post(url, {"label": "Full", "summary": "Full marks."}, headers=HTMX_HEADERS)
    method = Method.objects.get()
    assert response.headers["HX-Redirect"] == reverse("method_page", args=[method.pk])
    assert method.full_name == "bio1010/full"
    again = client.post(url, {"label": "Full", "summary": "Again."}, headers=HTMX_HEADERS)
    assert again.status_code == 422
    assert "This library already has a method called “Full”." in again.text, "a draft's name is taken too"
    assert re.search(r'name="label"[^>]*aria-invalid="true"', again.text), "on the name field"
    assert Method.objects.count() == 1


def test_publishing_under_a_new_name_renames_an_unused_method(library: Library, user: User) -> None:
    first = publish_light_method(library, user)
    second = publish_version(first.method, user, method_definition("Bright light"))
    first.method.refresh_from_db()
    assert first.method.name == "bright-light"
    assert (second.ref, MethodVersion.objects.get(pk=first.pk).ref) == (
        "bio1010/bright-light@2",
        "bio1010/bright-light@1",
    )
    assert definition_data(second.definition)["label"] == "Bright light"
    other = publish_light_method(library, user, name="other", label="Other")
    publish_version(other.method, user, method_definition("Light"))
    assert Method.objects.get(pk=other.method_id).name == "light", "free again"
    publish_version(first.method, user, method_definition("Bright light", answer="sun"))
    assert Method.objects.get(pk=first.method_id).name == "bright-light"


def test_publishing_under_a_new_name_keeps_a_used_methods_reference(
    assessment: Assessment,
    library: Library,
    user: User,
) -> None:
    used_by_marking = publish_light_method(library, user)
    save_marking(assessment, used_by_marking.ref, user)
    used_by_method = publish_light_method(library, user, name="base", label="Base")
    publish_parent(library, user, used_by_method)
    for version in (used_by_marking, used_by_method):
        renamed = publish_version(version.method, user, method_definition("Renamed " + version.method.name))
        assert renamed.ref == f"bio1010/{version.method.name}@2"
        assert services.label_of(renamed) == "Renamed " + version.method.name


def test_renaming_a_course_makes_its_namespace_again_while_its_methods_are_unused(
    client: Client,
    assessment: Assessment,
    library: Library,
    user: User,
) -> None:
    url = reverse("course_settings", args=[assessment.course_id])
    version = publish_light_method(library, user)
    client.post(url, {"name": "Introductory Biology", "description": ""})
    library.refresh_from_db()
    assert library.namespace == "introductory-biology"
    assert MethodVersion.objects.get(pk=version.pk).ref == "introductory-biology/light@1"
    save_marking(assessment, "introductory-biology/light@1", user)
    client.post(url, {"name": "Biology", "description": ""})
    library.refresh_from_db()
    assert (assessment.course.__class__.objects.get(pk=assessment.course_id).name, library.namespace) == (
        "Biology",
        "introductory-biology",
    )


def test_changing_an_email_address_makes_the_namespace_again_while_its_methods_are_unused(
    client: Client,
    assessment: Assessment,
    user: User,
) -> None:
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
    for address in ("ana.lim@example.edu", "ana.tan@example.edu"):
        EmailAddress.objects.create(user=user, email=address, verified=True)

    def namespace_after_making_primary(address: str) -> str:
        client.post(reverse("account_email"), {"action_primary": "", "email": address})
        user.library.refresh_from_db()
        return user.library.namespace

    version = publish_light_method(user.library, user)
    assert namespace_after_making_primary("ana.lim@example.edu") == "ana-lim-example-edu"
    save_marking(assessment, MethodVersion.objects.get(pk=version.pk).ref, user)
    assert namespace_after_making_primary("ana.tan@example.edu") == "ana-lim-example-edu"


def test_a_method_another_method_uses_says_which_where_it_cant_be_deleted(
    client: Client, library: Library, user: User
) -> None:
    version = publish_light_method(library, user)
    publish_parent(library, user, version)
    page = client.get(reverse("method_latest", args=[version.method_id]), follow=True).text
    assert 'title="Used by Parent, so archive it instead"' in page
