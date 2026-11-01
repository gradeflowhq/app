import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core.mail import EmailMessage
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses import views
from gradeflow_app.courses.invitations import accept_all, token
from gradeflow_app.courses.models import INVITATION_LIFETIME, Invitation, Member, Role
from gradeflow_app.live.topics import topic
from gradeflow_app.marking.models import Draft
from tests.conftest import HTMX, create_assessment, member, signed_in, start_marking

pytestmark = pytest.mark.django_db


def invite(client: Client, assessment: Assessment, email: str, role: Role = Role.VIEWER) -> Invitation:
    client.post(reverse("members", args=[assessment.course_id]), {"email": email, "role": role})
    return Invitation.objects.get(email=email.lower())


def test_viewers_change_nothing(client: Client, assessment: Assessment) -> None:
    _, draft = start_marking(client, assessment, "Q1", "gradeflow/key-points@1")
    question = assessment.questions.get(key="Q1")
    viewer = member(assessment, Role.VIEWER)
    owner = Member.objects.get(role=Role.OWNER)
    student = assessment.students.values_list("pk", flat=True).first()
    refused = {
        "adjust": (reverse("adjust", args=[assessment.pk, question.pk]), {"student": [str(student)], "marks": "0"}),
        "draft_save": (reverse("draft_save", args=[assessment.pk, draft.pk]), {"path": "", "op": "save"}),
        "member_role": (reverse("member_role", args=[assessment.course_id, owner.user_id]), {"role": Role.VIEWER}),
        "delete_assessment": (reverse("delete_assessment", args=[assessment.pk]), {}),
        "question_group_new": (
            reverse("question_group_new", args=[assessment.pk]),
            {"method": "gradeflow/custom-code-for-a-group@1"},
        ),
    }
    for name, (url, data) in refused.items():
        assert viewer.post(url, data).status_code == 403, name
    assert not assessment.questions.get(key="Q1").adjustments.exists()
    assert Draft.objects.get() == draft
    assert Assessment.objects.filter(pk=assessment.pk).exists()
    owner.refresh_from_db()
    assert owner.role == Role.OWNER


def test_assessment_pages_offer_only_what_the_role_may_do(client: Client, assessment: Assessment) -> None:
    overview = reverse("overview", args=[assessment.pk])
    settings = reverse("assessment_settings", args=[assessment.pk])
    viewer = member(assessment, Role.VIEWER).get(overview).content.decode()
    editor = member(assessment, Role.EDITOR, "cy@example.edu").get(overview).content.decode()
    owner = client.get(overview).content.decode()
    assert settings not in viewer
    assert settings not in editor
    assert settings in owner
    assert "Continue marking" not in viewer
    assert "Continue marking" in editor


def test_only_owners_manage_members(client: Client) -> None:
    assessment = create_assessment(client)
    invitation = invite(client, assessment, "cy@example.edu")
    editor = member(assessment, Role.EDITOR)
    owner = Member.objects.get(role=Role.OWNER)
    course = assessment.course_id
    refused = [
        (reverse("members", args=[course]), {"email": "dee@example.edu", "role": Role.OWNER}),
        (reverse("member_role", args=[course, owner.user_id]), {"role": Role.VIEWER}),
        (reverse("member_remove", args=[course, owner.user_id]), {}),
        (reverse("invitation_revoke", args=[course, invitation.pk]), {}),
        (reverse("invitation_resend", args=[course, invitation.pk]), {}),
        (reverse("invitation_role", args=[course, invitation.pk]), {"role": Role.OWNER}),
    ]
    for url, data in refused:
        assert editor.post(url, data).status_code == 403, url
    assert list(Invitation.objects.values_list("email", flat=True)) == ["cy@example.edu"]
    assert Member.objects.filter(user=owner.user, role=Role.OWNER).exists()


def test_a_course_keeps_an_owner(client: Client, user: User) -> None:
    assessment = create_assessment(client)
    course = assessment.course_id
    refused = {"level": "error", "text": "A course needs an owner. Make someone else an owner first."}
    for url, data in (
        (reverse("member_role", args=[course, user.pk]), {"role": Role.EDITOR}),
        (reverse("member_remove", args=[course, user.pk]), {}),
    ):
        assert json.loads(client.post(url, data, headers=HTMX).headers["X-Messages"]) == [refused]
    assert Member.objects.get(user=user).role == Role.OWNER

    other = member(assessment, Role.OWNER)
    demoted = client.post(reverse("member_role", args=[course, user.pk]), {"role": Role.EDITOR}, headers=HTMX)
    assert Member.objects.get(user=user).role == Role.EDITOR
    assert demoted["HX-Refresh"] == "true", "the whole page changes with their role"
    other.post(reverse("member_remove", args=[course, user.pk]))
    assert not Member.objects.filter(user=user).exists()


def test_the_only_owner_is_offered_no_role_change_or_leaving(client: Client, user: User) -> None:
    assessment = create_assessment(client)
    url = reverse("members", args=[assessment.course_id])
    page = client.get(url).content.decode()
    assert "Leave course" not in page
    assert f'aria-label="Role of {user}"' not in page

    member(assessment, Role.OWNER)
    page = client.get(url).content.decode()
    assert "Leave course" in page
    assert f'aria-label="Role of {user}"' in page


def test_leaving_a_course(client: Client, user: User) -> None:
    assessment = create_assessment(client)
    viewer = member(assessment, Role.VIEWER)
    left = viewer.post(reverse("leave_course", args=[assessment.course_id]), headers=HTMX)
    assert left["HX-Redirect"] == reverse("courses")
    assert not Member.objects.filter(user__email="bo@example.edu").exists()
    refused = client.post(reverse("leave_course", args=[assessment.course_id]), headers=HTMX)
    assert "A course needs an owner" in refused["X-Messages"]
    assert Member.objects.filter(user=user).exists()


def test_member_changes_refresh_the_members_page(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    assessment = create_assessment(client)
    invitation = invite(client, assessment, "cy@example.edu")
    member(assessment, Role.VIEWER)
    heard: list[str] = []
    monkeypatch.setattr(views, "publish", lambda *names: heard.extend(names))
    course = assessment.course_id
    client.post(reverse("member_remove", args=[course, Member.objects.get(role=Role.VIEWER).user_id]))
    client.post(reverse("invitation_resend", args=[course, invitation.pk]))
    client.post(reverse("invitation_revoke", args=[course, invitation.pk]))
    assert heard == [topic("members", course)] * 3


def test_member_changes_say_what_changed(client: Client) -> None:
    assessment = create_assessment(client)
    course = assessment.course_id
    member(assessment, Role.VIEWER)
    bo = Member.objects.get(user__email="bo@example.edu")

    def toasts(url: str, data: dict[str, object]) -> list[str]:
        return [message["text"] for message in json.loads(client.post(url, data, headers=HTMX)["X-Messages"])]

    members = reverse("members", args=[course])
    assert toasts(members, {"email": "Cy@Example.edu", "role": Role.VIEWER}) == ["Invited cy@example.edu."]
    invitation = Invitation.objects.get()
    assert toasts(reverse("invitation_resend", args=[course, invitation.pk]), {}) == [
        "Sent the invitation to cy@example.edu again."
    ]
    assert toasts(reverse("invitation_role", args=[course, invitation.pk]), {"role": Role.EDITOR}) == [
        "cy@example.edu's role is now Editor."
    ]
    assert Invitation.objects.get().role == Role.EDITOR
    assert toasts(reverse("member_role", args=[course, bo.user_id]), {"role": Role.OWNER}) == [
        "bo@example.edu's role is now Owner."
    ]
    assert toasts(members, {"email": "bo@example.edu", "role": Role.VIEWER}) == ["bo@example.edu is already a member."]
    assert Member.objects.get(pk=bo.pk).role == Role.OWNER


def test_invitations_say_when_they_expire(client: Client, mailoutbox: list[EmailMessage]) -> None:
    assessment = create_assessment(client)
    User.objects.create_user("cy@example.edu", "another-password")
    for email in ("bo@example.edu", "cy@example.edu"):
        client.post(reverse("members", args=[assessment.course_id]), {"email": email, "role": Role.VIEWER})
    invited, added = mailoutbox
    assert f"expires in {INVITATION_LIFETIME.days} days" in invited.body
    assert "expires" not in added.body


def test_accepting_an_invitation(client: Client) -> None:
    assessment = create_assessment(client)
    invitation = invite(client, assessment, "bo@example.edu", Role.EDITOR)
    invited = signed_in(User.objects.create_user("bo@example.edu", "another-password"))
    response = invited.get(reverse("invitation", args=[token(invitation)]))
    assert response.headers["Location"] == reverse("course", args=[assessment.course_id])
    assert Member.objects.get(user__email="bo@example.edu").role == Role.EDITOR
    again = invited.get(reverse("invitation", args=[token(invitation)]))
    assert again.headers["Location"] == reverse("course", args=[assessment.course_id]), "already a member"


def test_an_invitation_is_for_its_address_alone(client: Client) -> None:
    assessment = create_assessment(client)
    invitation = invite(client, assessment, "bo@example.edu")
    stranger = signed_in(User.objects.create_user("cy@example.edu", "another-password"))
    response = stranger.get(reverse("invitation", args=[token(invitation)]))
    assert response.status_code == 403
    assert b"This invitation is for bo@example.edu" in response.content
    assert not Member.objects.filter(user__email="cy@example.edu").exists()


def test_invitation_pages_lead_somewhere(client: Client) -> None:
    assessment = create_assessment(client)
    invitation = invite(client, assessment, "bo@example.edu")
    url = reverse("invitation", args=[token(invitation)])
    stranger = signed_in(User.objects.create_user("cy@example.edu", "another-password"))
    page = stranger.get(url).content.decode()
    assert "<title>This invitation is for bo@example.edu · GradeFlow</title>" in page
    assert f'<input type="hidden" name="next" value="{url}">' in page, "signing out comes back to the invitation"
    bogus = reverse("invitation", args=["nope"])
    assert f'href="{reverse("courses")}"' in stranger.get(bogus).content.decode()
    assert f'href="{reverse("account_login")}"' in Client().get(bogus).content.decode()


def test_signing_up_from_an_invitation_names_its_course(client: Client) -> None:
    assessment = create_assessment(client)
    url = reverse("invitation", args=[token(invite(client, assessment, "bo@example.edu"))])
    signup = Client().get(Client().get(url)["Location"]).content.decode()
    assert f"You're invited to <strong>{assessment.course.name}</strong>." in signup
    assert "You're invited" not in Client().get(reverse("account_signup")).content.decode()


def test_expired_invitations_are_refused_and_stay_open(client: Client) -> None:
    assessment = create_assessment(client)
    invitation = invite(client, assessment, "bo@example.edu")
    Invitation.objects.update(sent_at=timezone.now() - INVITATION_LIFETIME - timedelta(minutes=1))
    invited = User.objects.create_user("bo@example.edu", "another-password")
    assert signed_in(invited).get(reverse("invitation", args=[token(invitation)])).status_code == 404
    assert accept_all(invited, "bo@example.edu") == []
    invitation.refresh_from_db()
    assert invitation.accepted_at is None, "resending it can still bring them in"


def test_signing_in_to_accept_keeps_the_address_whole(client: Client) -> None:
    assessment = create_assessment(client)
    invitation = invite(client, assessment, "ana+tag@example.edu")
    url = reverse("invitation", args=[token(invitation)])
    query = parse_qs(urlsplit(Client().get(url).headers["Location"]).query)
    assert query == {"email": ["ana+tag@example.edu"], "next": [url]}
