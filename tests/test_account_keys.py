import base64
import json
import re
from html import unescape

import pytest
from django.core.mail import EmailMessage
from django.test import Client
from django.urls import reverse

from gradeflow_app.accounts.models import Secret, User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.keys import KEY_LOST, KEY_NOT_SHARED_YET
from gradeflow_app.courses.models import Course, CourseKey, Member, Role
from gradeflow_app.imports.models import Student
from tests.conftest import auth_key, new_account_fields, signed_in_client

ENCRYPTED_KEY = base64.b64encode(bytes(80)).decode()


def account_keys(page: str) -> dict[str, object]:
    match = re.search(r'<script id="account-keys" type="application/json">(.*?)</script>', page)
    assert match
    keys: dict[str, object] = json.loads(match.group(1))
    return keys


def salt_for(email: str) -> str:
    salt: str = Client().post(reverse("kdf_salt"), {"email": email}).json()["kdfSalt"]
    return salt


def test_signing_up_keeps_the_keys_made_in_the_browser(db: None, mailoutbox: list[EmailMessage]) -> None:
    fields = new_account_fields("bo-password")
    Client().post(reverse("account_signup"), {"name": "Bo Lim", "email": "bo@example.edu"} | fields)
    bo = User.objects.get(email="bo@example.edu")
    assert (bo.kdf_salt, bo.public_key, bo.wrapped_private_key) == (
        fields["kdf_salt"],
        fields["public_key"],
        fields["wrapped_private_key"],
    )
    assert bo.check_password(fields["password1"])
    assert salt_for("BO@example.edu") == fields["kdf_salt"]


def test_a_password_that_isnt_a_sign_in_key_is_refused(db: None) -> None:
    fields = new_account_fields("bo-password") | {"password1": "bo-password"}
    page = Client().post(reverse("account_signup"), {"name": "Bo Lim", "email": "bo@example.edu"} | fields).text
    assert "GradeFlow needs JavaScript to set a password." in page
    assert not User.objects.filter(email="bo@example.edu").exists()


def test_an_unknown_email_gets_the_same_salt_each_time(user: User) -> None:
    assert salt_for("nobody@example.edu") == salt_for("nobody@example.edu")
    assert salt_for("nobody@example.edu") != salt_for("somebody@example.edu")
    assert len(base64.b64decode(salt_for("nobody@example.edu"))) == 16


def test_signing_in_takes_the_sign_in_key(db: None) -> None:
    User.objects.create_user("bo@example.edu", auth_key("bo-password"))
    response = Client().post(reverse("account_login"), {"login": "bo@example.edu", "password": auth_key("bo-password")})
    assert response.status_code == 302


def test_changing_the_password_keeps_the_key_pair_under_the_new_one(user: User) -> None:
    user.set_password(auth_key("old-password"))
    user.public_key = "public"
    user.save()
    fields = new_account_fields("new-password")
    signed_in_client(user).post(
        reverse("account_change_password"),
        {"oldpassword": auth_key("old-password")}
        | {key: value for key, value in fields.items() if key != "public_key"},
    )
    user.refresh_from_db()
    assert user.check_password(fields["password1"])
    assert (user.kdf_salt, user.wrapped_private_key, user.public_key) == (
        fields["kdf_salt"],
        fields["wrapped_private_key"],
        "public",
    )


def reset_password(user: User, mailoutbox: list[EmailMessage], fields: dict[str, str]) -> str:
    """Resets the password by emailed code, sending `fields`. Returns the page that asks for the new password."""
    visitor = Client()
    visitor.post(reverse("account_reset_password"), {"email": user.email})
    code = re.findall(r"\b[A-Z0-9]{4}-[A-Z0-9]{4}\b", str(mailoutbox[-1].body))[0]
    page = visitor.post(reverse("account_confirm_password_reset_code"), {"code": code}, follow=True).text
    visitor.post(reverse("account_complete_password_reset"), fields)
    return page


@pytest.fixture
def user_with_keys(user: User) -> User:
    """`user`, holding a course key and a secret, with a recovery key."""
    user.public_key, user.recovery_wrapped_private_key = "public", "recovery-wrapped"
    user.save()
    member = Member.objects.create(course=Course.objects.create(name="BIO1010"), user=user, role=Role.OWNER)
    CourseKey.objects.create(member=member, encrypted_key=ENCRYPTED_KEY)
    Secret.objects.create(user=user, name=Secret.Name.CANVAS, encrypted_value=ENCRYPTED_KEY)
    return user


def test_resetting_the_password_without_the_recovery_key_starts_the_keys_again(
    user_with_keys: User, mailoutbox: list[EmailMessage]
) -> None:
    fields = new_account_fields("a-new-password")
    reset_password(user_with_keys, mailoutbox, fields)
    user_with_keys.refresh_from_db()
    assert (user_with_keys.kdf_salt, user_with_keys.public_key, user_with_keys.wrapped_private_key) == (
        fields["kdf_salt"],
        fields["public_key"],
        fields["wrapped_private_key"],
    )
    assert user_with_keys.recovery_wrapped_private_key == "", "it wraps the old private key"
    assert not CourseKey.objects.exists()
    assert not user_with_keys.secrets.exists()


def test_resetting_the_password_with_the_recovery_key_keeps_the_key_pair(
    user_with_keys: User, mailoutbox: list[EmailMessage]
) -> None:
    fields = new_account_fields("a-new-password") | {"public_key": "public"}
    page = reset_password(user_with_keys, mailoutbox, fields)
    assert 'data-recovery-wrapped-private-key="recovery-wrapped"' in page
    user_with_keys.refresh_from_db()
    assert (user_with_keys.wrapped_private_key, user_with_keys.recovery_wrapped_private_key) == (
        fields["wrapped_private_key"],
        "recovery-wrapped",
    )
    assert CourseKey.objects.exists()
    assert user_with_keys.secrets.exists()


def test_a_recovery_key_replaces_the_last_one(client: Client, user: User) -> None:
    url = reverse("save_recovery_key")
    assert client.post(url, {"recovery_wrapped_private_key": "first"}).status_code == 204
    assert client.post(url, {"recovery_wrapped_private_key": "second"}).status_code == 204
    assert client.post(url, {"recovery_wrapped_private_key": ""}).status_code == 400
    user.refresh_from_db()
    assert user.recovery_wrapped_private_key == "second"


def test_pages_carry_the_signed_in_persons_keys_and_secrets(client: Client, user: User) -> None:
    user.kdf_salt, user.public_key, user.wrapped_private_key = "salt", "public", "private"
    user.save()
    Secret.objects.create(user=user, name=Secret.Name.CANVAS, encrypted_value="encrypted")
    keys = account_keys(client.get(reverse("courses")).text)
    assert keys == {
        "userId": str(user.pk),
        "kdfSalt": "salt",
        "publicKey": "public",
        "wrappedPrivateKey": "private",
        "encryptedSecrets": {"canvas": "encrypted"},
    }
    assert 'id="account-keys"' not in Client().get(reverse("account_login")).text


def test_secrets_are_kept_replaced_and_forgotten(client: Client, user: User) -> None:
    url = reverse("secret", args=["ai-service"])
    assert client.post(url, {"encrypted_value": ENCRYPTED_KEY}).status_code == 204
    assert client.post(url, {"encrypted_value": base64.b64encode(b"new").decode()}).status_code == 204
    assert user.secrets.get().encrypted_value == base64.b64encode(b"new").decode()
    assert client.post(url, {"encrypted_value": ""}).status_code == 400
    assert client.post(reverse("secret", args=["github"]), {"encrypted_value": ENCRYPTED_KEY}).status_code == 404
    assert client.delete(url).status_code == 204
    assert not user.secrets.exists()


@pytest.fixture
def course(user: User) -> Course:
    course = Course.objects.create(name="BIO1010")
    Member.objects.create(course=course, user=user, role=Role.OWNER)
    Assessment.objects.create(course=course, name="Midterm")
    return course


def course_key_on_page(client: Client, course: Course) -> tuple[str, str]:
    """The encrypted course key and unavailable reason on an assessment page in `course`."""
    page = client.get(reverse("overview", args=[course.assessments.get().pk])).text
    match = re.search(r'data-encrypted-course-key="([^"]*)" data-course-key-unavailable-reason="([^"]*)"', page)
    assert match
    return match.group(1), unescape(match.group(2))


def test_the_first_import_gives_the_course_its_key_once(client: Client, course: Course) -> None:
    assert course_key_on_page(client, course) == ("", "")
    key_url = reverse("create_course_key", args=[course.pk])
    assert client.post(key_url, {"encrypted_key": ENCRYPTED_KEY}).status_code == 204
    assert course_key_on_page(client, course) == (ENCRYPTED_KEY, "")
    assert client.post(key_url, {"encrypted_key": ENCRYPTED_KEY}).status_code == 409


def test_a_lost_course_key_is_never_replaced_while_ids_need_it(client: Client, course: Course) -> None:
    Student.objects.create(assessment=course.assessments.get(), encrypted_id="enc:v1:aaaa:bbbb", answers={})
    assert course_key_on_page(client, course) == ("", KEY_LOST)
    assert (
        client.post(reverse("create_course_key", args=[course.pk]), {"encrypted_key": ENCRYPTED_KEY}).status_code == 409
    )


def test_a_viewer_cant_start_the_course_key_and_outsiders_cant_post_one(course: Course, bo: User) -> None:
    other = signed_in_client(bo)
    key_url = reverse("create_course_key", args=[course.pk])
    assert other.post(key_url, {"encrypted_key": ENCRYPTED_KEY}).status_code == 404
    Member.objects.create(course=course, user=bo, role=Role.VIEWER)
    assert other.post(key_url, {"encrypted_key": ENCRYPTED_KEY}).status_code == 403


def key_shares(client: Client) -> object:
    page = client.get(reverse("courses")).text
    match = re.search(r'<script id="pending-course-key-shares" type="application/json">(.*?)</script>', page)
    assert match
    return json.loads(match.group(1))


def test_members_with_the_key_hand_it_to_those_without(client: Client, user: User, course: Course, bo: User) -> None:
    CourseKey.objects.create(member=course.members.get(user=user), encrypted_key=ENCRYPTED_KEY)
    bo_member = Member.objects.create(course=course, user=bo, role=Role.VIEWER)
    assert key_shares(client) == [], "Bo has no public key to encrypt it for yet"
    bo.public_key = "bo-public"
    bo.save()
    assert key_shares(client) == [
        {
            "courseId": str(course.pk),
            "userId": str(bo.pk),
            "publicKey": "bo-public",
            "encryptedCourseKey": ENCRYPTED_KEY,
        }
    ]
    other = signed_in_client(bo)
    assert course_key_on_page(other, course) == ("", KEY_NOT_SHARED_YET)
    url = reverse("share_course_key", args=[course.pk, bo.pk])
    share = {"public_key": "bo-public", "encrypted_key": ENCRYPTED_KEY}
    assert key_shares(other) == []
    assert other.post(url, share).status_code == 404, "Bo has no key to hand over"
    stale = share | {"public_key": "bo-old-public"}
    assert client.post(url, stale).status_code == 404, "encrypted for a public key Bo no longer has"
    assert client.post(url, share).status_code == 204
    assert CourseKey.objects.get(member=bo_member).encrypted_key == ENCRYPTED_KEY
    assert key_shares(client) == []
    assert course_key_on_page(other, course) == (ENCRYPTED_KEY, "")


def test_an_invite_lists_the_new_member_for_sharing_at_once(
    client: Client, user: User, course: Course, bo: User
) -> None:
    CourseKey.objects.create(member=course.members.get(user=user), encrypted_key=ENCRYPTED_KEY)
    bo.public_key = "bo-public"
    bo.save()
    response = client.post(reverse("members", args=[course.pk]), {"email": bo.email, "role": Role.VIEWER})
    match = re.search(
        r'<script hx-swap-oob="true" id="pending-course-key-shares" type="application/json">(.*?)</script>',
        response.text,
    )
    assert match
    assert json.loads(match.group(1)) == [
        {
            "courseId": str(course.pk),
            "userId": str(bo.pk),
            "publicKey": "bo-public",
            "encryptedCourseKey": ENCRYPTED_KEY,
        }
    ]


def test_handing_over_needs_a_member_on_both_ends(client: Client, user: User, course: Course, bo: User) -> None:
    CourseKey.objects.create(member=course.members.get(user=user), encrypted_key=ENCRYPTED_KEY)
    url = reverse("share_course_key", args=[course.pk, bo.pk])
    assert client.post(url, {"public_key": "", "encrypted_key": ENCRYPTED_KEY}).status_code == 400
    assert client.post(url, {"public_key": "bo-public", "encrypted_key": ENCRYPTED_KEY}).status_code == 404


def test_a_removed_member_loses_their_copy(client: Client, course: Course, bo: User) -> None:
    member = Member.objects.create(course=course, user=bo, role=Role.VIEWER)
    CourseKey.objects.create(member=member, encrypted_key=ENCRYPTED_KEY)
    client.post(reverse("member_remove", args=[course.pk, bo.pk]))
    assert not CourseKey.objects.exists()
