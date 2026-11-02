import json
import re
import time
from typing import Any

import pytest
from allauth.mfa.models import Authenticator
from allauth.mfa.totp.internal.auth import format_hotp_value, hotp_value
from allauth.usersessions.models import UserSession
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape, strip_tags
from gradeflow_core import BUILTINS, InvalidError, Question
from gradeflow_core.editing import QUESTION_TYPES, QuestionType, dump_yaml, infer_question_as, new_question
from pytest_django import DjangoCaptureOnCommitCallbacks

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.editor.guide import guide_context, used_for
from gradeflow_app.libraries.collection import in_use
from gradeflow_app.marking.checks import check_of, tree_of
from gradeflow_app.marking.models import Draft, Marking
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.publish.models import Publication
from gradeflow_app.questions import views as question_views
from gradeflow_app.questions.forms import QuestionForm
from gradeflow_app.results.models import Adjustment, Result
from gradeflow_app.ui.templatetags.ui import markdown, marks, math_markdown
from tests.conftest import (
    HTMX,
    RESULTS,
    code_draft,
    create_assessment,
    envelope,
    marked,
    signed_in,
    start_marking,
    upload,
)

pytestmark = pytest.mark.django_db


def test_creating_a_course_makes_its_creator_owner(client: Client) -> None:
    assessment = create_assessment(client)
    assert assessment.course.members.get().role == Role.OWNER


def test_importing_results_infers_questions_and_keeps_file_marks(client: Client, assessment: Assessment) -> None:
    assert [q.key for q in assessment.questions.all()] == ["Q1", "Q2", "Q3"]
    assert assessment.students.count() == 10
    assert not Result.objects.exists(), "no marking marks them yet"
    file_marks = [student.file_marks.get("Q1") for student in assessment.students.all()]
    assert sorted(file_marks) == [0] * 6 + [1] * 4


def test_marking_a_question_with_key_points(
    client: Client, assessment: Assessment, django_capture_on_commit_callbacks: DjangoCaptureOnCommitCallbacks
) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    response = client.post(url, {"path": "", "fields": "1", "points": ["light"], "combine": "proportional"})
    assert response.status_code == 200
    assert b'id="preview-bar"' in response.content, "a changed draft offers to preview it"
    assert b"Finish the marking to preview" not in response.content
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    assert response.status_code == 200
    marking = Marking.objects.get(question=draft.question)
    check = check_of(marking.definition, in_use(assessment.course))
    assert check is not None
    assert tree_of(marking.definition) == check.dump(), "saved as the core writes it"
    results = Result.objects.filter(question=draft.question)
    assert results.count() == assessment.students.count()
    assert set(results.values_list("marking", flat=True)) == {marking.pk}


def test_saving_an_incomplete_marking_shows_what_is_missing(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    response = client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    assert response.status_code == 200
    assert not Marking.objects.exists()
    missing = [escape(problem.message) for problem in Workspace(draft).checks.problems]
    assert missing
    assert all(message in response.content.decode() for message in missing)


def test_combining_checks(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    client.post(url, {"path": "", "op": "add", "target": "with.checks.0", "value": "gradeflow/key-points@1"})
    draft.refresh_from_db()
    assert tree_of(draft.definition)["with"]["checks"] == [{"uses": "gradeflow/key-points@1", "with": {}}]
    client.post(url, {"path": "with.checks.0", "fields": "1", "points": ["light"], "combine": "all"})
    client.post(reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"})
    assert Marking.objects.filter(question=draft.question).exists()


def test_adjusting_a_group(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    students = list(assessment.students.values_list("pk", flat=True)[:2])
    response = client.post(
        reverse("adjust", args=[assessment.pk, question.pk]),
        {"student": [str(s) for s in students], "marks": "0.5", "feedback": "Close"},
        headers=HTMX,
    )
    assert response.status_code == 200
    assert Adjustment.objects.filter(question=question).count() == 2
    assert {"level": "success", "text": "Adjusted 2 students."} in json.loads(response.headers["X-Messages"])
    response = client.post(
        reverse("adjust", args=[assessment.pk, question.pk]), {"student": [str(students[0])], "marks": "9"}
    )
    assert response.status_code == 422


def test_pages_render(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    pages: list[tuple[str, list[object]]] = [
        ("courses", []),
        ("course", [assessment.course_id]),
        ("course_settings", [assessment.course_id]),
        ("overview", [assessment.pk]),
        ("members", [assessment.course_id]),
        ("canvas", [assessment.course_id]),
        ("assessment_settings", [assessment.pk]),
        ("import", [assessment.pk]),
        ("question", [assessment.pk, question.pk]),
        ("answers", [assessment.pk, question.pk]),
        ("question_settings", [assessment.pk, question.pk]),
        ("students", [assessment.pk]),
        ("publish", [assessment.pk]),
        ("question_group_new", [assessment.pk]),
        ("course_methods", [assessment.course_id]),
        ("settings", []),
    ]
    for name, args in pages:
        response = client.get(reverse(name, args=args))
        assert response.status_code == 200, (name, response.status_code)


def test_parts_of_a_page_opened_on_their_own_go_to_the_page(client: Client, assessment: Assessment) -> None:
    student = assessment.students.all()[0]
    students = reverse("students", args=[assessment.pk])
    publish = reverse("publish", args=[assessment.pk])
    parts = {
        reverse("student", args=[assessment.pk, student.pk]): students,
        reverse("publish_rows", args=[assessment.pk]): publish,
        reverse("publish_comment", args=[assessment.pk]): publish,
        reverse("activity", args=[assessment.pk]): reverse("overview", args=[assessment.pk]),
    }
    for part, page in parts.items():
        assert client.get(part, headers=HTMX).status_code == 200, part
        response = client.get(part)
        assert (response.status_code, response["Location"]) == (302, page), part


def test_download_has_envelopes_for_the_browser_to_decrypt(client: Client, assessment: Assessment) -> None:
    response = client.post(reverse("download", args=[assessment.pk]), {"student_id_column": "student_id"})
    assert response.status_code == 200
    assert envelope("S001").encode() in response.content


def test_non_members_get_not_found(client: Client) -> None:
    assessment = create_assessment(client)
    other = signed_in(User.objects.create_user("bo@example.edu", "another-password"))
    assert other.get(reverse("overview", args=[assessment.pk])).status_code == 404


def test_a_multi_value_question_has_one_check_per_value(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    question.core = Question.load(
        {"type": "values", "value_types": ["text", "number"], "max_marks": question.core.max_marks}
    )
    question.save()
    editor = client.post(
        reverse("marking_start", args=[assessment.pk, question.pk]), {"method": "gradeflow/multiple-values@1"}
    )
    assert editor.status_code == 200
    assert b"Add check" not in editor.content
    assert b'"op": "remove"' not in editor.content
    assert b'"op": "move"' not in editor.content


def test_a_methods_guide_says_what_the_core_says_of_it(client: Client, assessment: Assessment) -> None:
    url = reverse("method_guide", args=["gradeflow/key-points@1"])
    assert client.get(url).status_code == 404, "it's part of the picker"
    page = client.get(url, {"course": str(assessment.course_id)}, headers=HTMX).content.decode()
    method = BUILTINS.find("gradeflow/key-points@1")
    assert method.guide
    assert math_markdown(method.guide) in page
    assert used_for(method) in page


def test_a_methods_guide_shows_what_its_examples_get(client: Client, assessment: Assessment) -> None:
    page = client.get(reverse("method_guide", args=["gradeflow/number-range@1"]), headers=HTMX).content.decode()
    examples = BUILTINS.find("gradeflow/number-range@1").examples
    results = [(row.expected["Q1"], example.questions["Q1"]) for example in examples for row in example.rows]
    assert results
    for result, question in results:
        assert escape(result.feedback) in page
        assert (
            f'{marks(result.marks)}</span><span class="font-normal text-muted">/ {marks(question.max_marks)}</span>'
            in page
        )


def test_a_methods_guide_shows_each_questions_feedback_and_the_total(client: Client, assessment: Assessment) -> None:
    page = client.get(reverse("method_guide", args=["gradeflow/answer-sets@1"]), headers=HTMX).content.decode()
    assert '<span class="text-subtle">Q2</span> Expected 25.' in page
    assert f'{marks(1)}</span><span class="font-normal text-muted">/ {marks(2)}</span>' in page


def test_a_methods_guide_leaves_out_examples_without_a_result() -> None:
    blank = {
        "title": "Examples",
        "description": "",
        "questions": {"Q1": {"type": "text"}},
        "rows": [{"answers": {"Q1": ""}}],
    }
    check = {"uses": "gradeflow/key-points@1", "with": {"points": ["light"]}}
    text = dump_yaml(
        {
            "version": 1,
            "label": "Light",
            "summary": "Says light.",
            "question_types": ["text"],
            "checks": [check],
            "examples": [blank],
        }
    )
    methods = BUILTINS.with_files([("lab/light", text)])
    context = guide_context(methods[-1].ref, methods)
    assert context["examples"] == []
    shown = render_to_string("marking/method_body.html", context)
    assert "No guide or examples yet." in shown
    assert "Blank" not in shown


def test_unknown_methods_have_no_guide(client: Client, assessment: Assessment) -> None:
    assert client.get(reverse("method_guide", args=["gradeflow/nope@1"]), headers=HTMX).status_code == 404


def test_a_question_group_with_answer_sets(client: Client, assessment: Assessment) -> None:
    response = client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/answer-sets@1"})
    assert client.get(response.headers["Location"]).status_code == 200
    draft = Draft.objects.get()
    url = reverse("draft", args=[assessment.pk, draft.pk])
    assert tree_of(draft.definition)["with"]["sets"] == [
        {"name": "Expected", "uses": "gradeflow/combination@1", "with": {"checks": []}}
    ], "starting with one answer_set"
    target = "with.sets.0.with.checks.0"
    response = client.post(
        url,
        {
            "path": "with.sets.0",
            "op": "add",
            "target": target,
            "value": "gradeflow/key-points@1",
            "question": "Q1",
        },
    )
    assert response.status_code == 200
    draft.refresh_from_db()
    assert tree_of(draft.definition)["with"]["sets"][0]["with"]["checks"] == [
        {"question": "Q1", "uses": "gradeflow/key-points@1", "with": {}}
    ], "a question group's check reads the question chosen with its method"


def test_course_members_work_on_all_its_assessments(client: Client) -> None:
    assessment = create_assessment(client)
    other = signed_in(User.objects.create_user("bo@example.edu", "another-password"))
    client.post(reverse("members", args=[assessment.course_id]), {"email": "bo@example.edu", "role": Role.VIEWER})
    assert other.get(reverse("overview", args=[assessment.pk])).status_code == 200
    assert other.post(reverse("course", args=[assessment.course_id]), {"name": "Final"}).status_code == 403
    assert not assessment.course.assessments.filter(name="Final").exists(), "viewers can't add assessments"


def test_the_question_list_refreshes_on_its_own(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    question_page = reverse("question", args=[assessment.pk, question.pk])
    response = client.get(reverse("question_list", args=[assessment.pk]), {"shown": str(question.pk)})
    assert response.status_code == 200
    assert f'href="{question_page}" class="list-link" aria-current="page"'.encode() in response.content
    assert b'id="answers"' not in response.content


def test_account_pages_speak_plainly(client: Client) -> None:
    password = client.get(reverse("account_change_password")).content
    assert b"Current password" in password
    assert b'placeholder="Current Password"' not in password
    devices = client.get(reverse("usersessions_list")).content
    assert b"This device" in devices


def test_a_name_is_required_as_at_sign_up(client: Client, user: User) -> None:
    response = client.post(reverse("settings"), {"name": " "})
    assert response.status_code == 200
    assert "(optional)" not in response.content.decode()
    user.refresh_from_db()
    assert user.name


def test_verifying_an_added_address_returns_to_the_addresses(client: Client, mailoutbox: list[EmailMessage]) -> None:
    client.post(reverse("account_email"), {"action_add": "", "email": "second@example.edu"})
    (code,) = re.findall(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", str(mailoutbox[-1].body))
    response = client.post(reverse("account_email_verification_sent"), {"code": code})
    assert response["Location"] == reverse("account_email")


def test_the_primary_address_and_this_device_come_first(client: Client, user: User) -> None:
    client.post(reverse("account_email"), {"action_add": "", "email": "aaa@example.edu"})
    page = client.get(reverse("account_email")).content.decode()
    assert page.index(user.email) < page.index("aaa@example.edu")
    signed_in(user).get(reverse("courses"))
    UserSession.objects.filter(session_key=client.session.session_key).update(created_at=timezone.now())
    page = client.get(reverse("usersessions_list")).content.decode()
    assert page.index("This device") < page.rindex("Last seen")


def test_signing_out_another_device(client: Client, user: User) -> None:
    other = signed_in(user)
    other.get(reverse("courses"))
    client.get(reverse("courses"))
    device = UserSession.objects.get(session_key=other.session.session_key)
    client.post(reverse("sign_out_device", args=[device.pk]))
    assert not UserSession.objects.filter(pk=device.pk).exists()
    assert other.get(reverse("courses")).status_code == 302
    this = UserSession.objects.get(session_key=client.session.session_key)
    assert client.post(reverse("sign_out_device", args=[this.pk])).status_code == 404


def test_a_wrong_password_is_named_plainly(user: User) -> None:
    page = Client().post(reverse("account_login"), {"login": user.email, "password": "wrong"}).content.decode()
    assert "Wrong email or password." in page


def test_the_courses_list_names_roles_only_when_they_differ(client: Client, user: User) -> None:
    create_assessment(client)
    assert "Owner" not in client.get(reverse("courses")).content.decode()
    other = signed_in(User.objects.create_user("bo@example.edu", "another-password"))
    other.post(reverse("courses"), {"name": "Chemistry"})
    Member.objects.create(course=Course.objects.get(name="Chemistry"), user=user, role=Role.VIEWER)
    page = client.get(reverse("courses")).content.decode()
    assert "Owner" in page
    assert "Viewer" in page


def test_a_course_card_counts_its_assessments_by_what_each_needs(client: Client, user: User) -> None:
    course = create_assessment(client, "Quiz 1").course
    marked(client, course, "Quiz 2", ["Q1"])
    sent = marked(client, course, "Quiz 3", ["Q1"])
    Publication.objects.create(assessment=sent, sent_by=user, assignment_name="Quiz 3", student_count=3)
    client.post(reverse("courses"), {"name": "Chemistry"})
    text = " ".join(strip_tags(client.get(reverse("courses")).content.decode()).split())
    assert "BIO1010 3 assessments 1 to mark 1 ready to send 1 sent" in text
    assert "Chemistry No assessments yet" in text


def test_the_courses_list_offers_a_search_once_there_are_many_courses(client: Client) -> None:
    for number in range(8):
        client.post(reverse("courses"), {"name": f"Course {number}"})
    assert "Search courses" not in client.get(reverse("courses")).content.decode()
    client.post(reverse("courses"), {"name": "Course 8"})
    assert "Search courses" in client.get(reverse("courses")).content.decode()
    matches = client.get(reverse("courses"), {"q": "Course 3"}).content.decode()
    assert "Course 3" in matches
    assert "Course 4" not in matches


def test_an_inactive_account_is_told_so_plainly() -> None:
    page = Client().get(reverse("account_inactive")).content.decode()
    assert "<title>Account access · GradeFlow</title>" in page
    assert "Account Inactive" not in page


def test_signing_in_and_up_speak_plainly() -> None:
    visitor = Client()
    signup = visitor.get(reverse("account_signup")).content.decode()
    assert "At least 8 characters." in signup
    assert "commonly used" not in signup
    for page in ("account_signup", "account_login", "account_reset_password", "account_request_login_code"):
        assert "placeholder=" not in visitor.get(reverse(page)).content.decode(), page


def test_the_sign_in_code_page_is_titled_like_the_others(user: User) -> None:
    visitor = Client()
    visitor.post(reverse("account_request_login_code"), {"email": user.email})
    page = visitor.get(reverse("account_confirm_login_code")).content.decode()
    assert "<title>Enter your sign-in code · GradeFlow</title>" in page
    assert f"We sent a code to {user.email}." in page


def test_every_code_page_asks_for_its_code_alike(user: User) -> None:
    visitor = Client()
    visitor.post(reverse("account_reset_password"), {"email": user.email})
    page = visitor.post(reverse("account_confirm_password_reset_code"), {"code": "WRONG"}).content.decode()
    assert "placeholder=" not in page
    assert 'class="flex items-center gap-1.5 text-sm text-error"><svg' in page


def test_resetting_a_password_ends_with_signing_in(user: User, mailoutbox: list[EmailMessage]) -> None:
    visitor = Client()
    visitor.post(reverse("account_reset_password"), {"email": user.email})
    (code,) = re.findall(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", str(mailoutbox[-1].body))
    page = visitor.post(reverse("account_confirm_password_reset_code"), {"code": code}, follow=True).content.decode()
    assert "<title>Reset your password · GradeFlow</title>" in page
    assert "Change Password" not in page
    done = visitor.post(
        reverse("account_complete_password_reset"),
        {"password1": "a-new-password-2026", "password2": "a-new-password-2026"},
        follow=True,
    ).content.decode()
    assert "Password changed" in done
    assert f'href="{reverse("account_login")}"' in done


def test_marking_is_chosen_by_how_to_mark(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    page = client.get(reverse("question", args=[assessment.pk, question.pk])).content.decode()
    assert "Choose a method" in page
    picker = client.get(reverse("question_methods", args=[assessment.pk, question.pk])).content.decode()
    assert 'placeholder="Search"' in picker
    assert "Nothing matches." in picker


def test_more_options_stay_open_while_their_rows_change(client: Client, assessment: Assessment) -> None:
    url, fields = code_draft(client, assessment)
    response = client.post(url, fields | {"op": "add_row", "target": "parameters", "more": "1"})
    assert b'class="group/more" open' in response.content
    response = client.post(url, fields | {"op": "add_row", "target": "parameters"})
    assert b'class="group/more" open' not in response.content, "closed unless the change came from inside"


def test_complete_answer_sets_say_what_they_check(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/answer-sets@1"})
    draft = Draft.objects.get()
    check = {"question": "Q1", "uses": "gradeflow/accepted-answers@1", "with": {"answers": ["light"]}}
    answer_set = {"name": "Correct", "uses": "gradeflow/combination@1", "with": {"checks": [check]}}
    draft.definition = dump_yaml({"uses": "gradeflow/answer-sets@1", "with": {"sets": [answer_set]}})
    draft.save()
    page = client.get(reverse("question_group_draft", args=[assessment.pk, draft.pk])).content.decode()
    assert markdown(Workspace(draft).checks.describe("with.sets.0")) in page
    assert "Needs setting up" not in page


def test_an_empty_combination_inside_another_needs_setting_up(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q1", "gradeflow/combination@1")
    inner = {"uses": "gradeflow/combination@1", "with": {}}
    draft.definition = dump_yaml({"question": "Q1", "uses": "gradeflow/combination@1", "with": {"checks": [inner]}})
    draft.save()
    page = client.get(reverse("question", args=[assessment.pk, draft.question_id])).content.decode()
    assert "All of these:" not in page
    assert "Needs setting up" in page


def test_passkey_pages_speak_plainly(client: Client, user: User) -> None:
    confirm = client.get(reverse("account_reauthenticate")).content.decode()
    assert "Confirm it's you" in confirm
    assert "Confirm Access" not in confirm
    Authenticator.objects.create(
        user=user, type=Authenticator.Type.WEBAUTHN, data={"name": "Work laptop", "credential": {}}
    )
    passkeys = client.get(reverse("mfa_list_webauthn")).content.decode()
    assert "Security Keys" not in passkeys
    assert "Work laptop" in passkeys
    assert 'aria-label="Actions for Work laptop"' in passkeys
    assert 'data-confirm="Remove Work laptop?"' not in passkeys


def test_turning_the_authenticator_app_on_and_off(client: Client, user: User) -> None:
    client.post(reverse("account_reauthenticate"), {"password": "a-long-password"})
    page = client.get(reverse("mfa_activate_totp"))
    secret = page.context["form"].secret
    assert "Turn on" in page.content.decode()
    assert "placeholder=" not in page.content.decode()
    code = format_hotp_value(hotp_value(secret, int(time.time()) // 30))
    response = client.post(reverse("mfa_activate_totp"), {"code": code})
    assert response.headers["Location"] == reverse("mfa_view_recovery_codes")
    codes = client.get(reverse("mfa_view_recovery_codes"))
    assert all(f"<li>{code}</li>" in codes.content.decode() for code in codes.context["unused_codes"])
    client.post(reverse("mfa_deactivate_totp"))
    assert not Authenticator.objects.filter(user=user).exists()


def test_the_question_list_shows_unsaved_drafts(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q2", "gradeflow/accepted-answers@1")
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/custom-code-for-a-group@1"})
    group = Draft.objects.get(question=None)
    listed = client.get(reverse("question", args=[assessment.pk, draft.question_id])).content.decode()
    assert reverse("question_group_draft", args=[assessment.pk, group.pk]) in listed, (
        "an unsaved question group stays reachable"
    )
    assert listed.count(">Draft</span>") == 2
    assert ">Accepted Answers</span>" in listed, "the method being drafted"
    assert "0 of" in listed, "a draft marks nothing yet"


def test_multiple_values_are_listed_in_order_and_may_repeat(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q3")
    answers = [answer for *_, answer in RESULTS]
    set_up = infer_question_as(question.core, "values", answers)
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    form = client.get(url, {"question_type": "values"}).content.decode()
    assert 'x-data="orderedChoices"' in form
    client.post(url, {"question_type": "values", "save": "1", "max_marks": "1", "value_types": ["number", "number"]})
    question.refresh_from_db()
    assert question.definition["value_types"] == ["number", "number"]
    assert question.definition["reading"]["separator"] == set_up.model_dump(mode="json")["reading"]["separator"], (
        "an option left out of the post keeps its setup"
    )


def test_multiple_values_accept_a_space_separator(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q3")
    response = client.post(
        reverse("question_settings", args=[assessment.pk, question.pk]),
        {
            "question_type": "values",
            "save": "1",
            "max_marks": "1",
            "value_types": ["text", "text"],
            "reading__separator": " ",
        },
    )
    assert response.status_code == 200
    question.refresh_from_db()
    assert question.definition["reading"]["separator"] == " "


def test_each_answer_type_says_what_it_is(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q2")
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    assert escape(QUESTION_TYPES[question.core.type].summary) in client.get(url).content.decode()


def rejection(question_type: QuestionType, values: dict[str, Any]) -> str:
    """Why the core rejects a question."""
    with pytest.raises(InvalidError) as rejected:
        new_question(question_type, values)
    [problem] = rejected.value.problems
    return escape(problem.message)


def test_a_question_the_core_rejects_says_why(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q3")
    before = question.definition
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    response = client.post(
        url,
        {
            "question_type": "choice",
            "save": "1",
            "max_marks": "1",
            "options": ["Yes", "yes"],
            "reading__ignore_case": "on",
        },
    )
    errors = response.content.decode().split('id="id_options_error"', 1)[1].split("</div>", 1)[0]
    assert rejection("choice", {"options": ["Yes", "yes"], "reading": {"ignore_case": True}}) in errors
    question.refresh_from_db()
    assert question.definition == before


def test_a_cleared_question_setting_says_what_the_core_needs(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q3")
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    response = client.post(url, {"question_type": "values", "save": "1", "max_marks": "1"})
    errors = response.content.decode().split('id="id_value_types_error"', 1)[1].split("</div>", 1)[0]
    assert rejection("values", {"value_types": []}) in errors


def test_changing_only_the_maximum_marks_rescales_without_marking_again(
    client: Client, assessment: Assessment, monkeypatch: pytest.MonkeyPatch
) -> None:
    remarked: list[list[str]] = []
    monkeypatch.setattr(question_views, "remark", lambda _assessment, keys, _user, _endpoint: remarked.append(keys))
    question = assessment.questions.get(key="Q2")
    form = QuestionForm(question.core)
    shown = {name: form[name].value() for name in form.fields}
    data = {name: "on" if value is True else value for name, value in shown.items() if value not in (None, False)}
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    client.post(url, data | {"question_type": question.core.type, "save": "1", "max_marks": "3"})
    question.refresh_from_db()
    assert question.core.max_marks == 3
    assert remarked == []
    client.post(
        url, data | {"question_type": question.core.type, "save": "1", "max_marks": "3", "description": "Changed"}
    )
    assert remarked == [["Q2"]]


def test_a_cleared_setting_with_a_default_is_not_quietly_reset(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    question.definition |= {"max_marks": 4}
    question.save()
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    response = client.post(url, {"question_type": question.core.type, "save": "1", "max_marks": ""})
    question.refresh_from_db()
    assert question.core.max_marks == 4
    assert 'id="id_max_marks_error"' in response.content.decode()


def test_a_chosen_type_is_set_up_from_the_answers(client: Client) -> None:
    assessment = create_assessment(client)
    rows = [f'{envelope(student)},"{answer}"' for student, answer in (("S1", "3, -2"), ("S2", "2, -3"))]
    upload(client, assessment, "\n".join(["StudentID,Q1", *rows]), answer_columns='["Q1"]', marks_columns="{}")
    question = assessment.questions.get(key="Q1")
    form = client.get(reverse("question_settings", args=[assessment.pk, question.pk]), {"question_type": "values"})
    inferred = infer_question_as(question.core, "values", ["3, -2", "2, -3"]).model_dump()["value_types"]
    page = form.content.decode()
    assert inferred
    assert all(page.count(f'<option value="{kind}" selected>') == inferred.count(kind) for kind in set(inferred))


def test_a_chosen_type_keeps_the_marks_and_description(client: Client, assessment: Assessment) -> None:
    question = assessment.questions.get(key="Q1")
    question.definition |= {"max_marks": 3, "description": "What plants need"}
    question.save()
    url = reverse("question_settings", args=[assessment.pk, question.pk])
    form = client.get(url, {"question_type": "number"}).content.decode()
    assert 'value="3"' in form
    assert "What plants need" in form


def test_choosing_a_checks_question_redraws_its_form(client: Client, assessment: Assessment) -> None:
    client.post(reverse("question_group_new", args=[assessment.pk]), {"method": "gradeflow/conditional@1"})
    url = reverse("draft", args=[assessment.pk, Draft.objects.get().pk])
    target = "with.condition.0"
    client.post(url, {"path": "", "op": "add", "target": target, "value": "gradeflow/full-marks@1", "question": "Q1"})
    fields = {"path": target, "fields": "1", "question": "Q2"}
    redrawn = client.post(url, fields)
    assert b'id="marking-panel"' in redrawn.content, "a number question offers other methods"
    assert (redrawn["HX-Retarget"], redrawn["HX-Reswap"]) == ("#marking-panel", "outerMorph"), (
        "in place: no scroll jump"
    )
    typed = client.post(url, fields)
    assert b'id="marking-panel"' not in typed.content, "nothing else changed"
    assert "HX-Retarget" not in typed


def test_another_method_replaces_the_checks(client: Client, assessment: Assessment) -> None:
    url, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    client.post(url, {"path": "", "op": "retype", "target": "", "value": "gradeflow/combination@1"})
    draft.refresh_from_db()
    assert tree_of(draft.definition)["uses"] == "gradeflow/combination@1"


def test_more_options_hold_only_the_advanced_fields(client: Client, assessment: Assessment) -> None:
    url, _ = code_draft(client, assessment)
    panel = client.get(url).content.decode()
    assert panel.count('name="code"') == 1, "the code isn't repeated under More options"
