"""Course keys. Each course has a random key that encrypts its student IDs. Each member stores it encrypted for their
public key; the browser creates and shares it (static/gradeflow/course-keys.js), so the server never sees it."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from allauth.account.signals import password_reset
from django import forms
from django.db import transaction
from django.dispatch import receiver
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.access import course_member
from gradeflow_app.courses.models import Course, CourseKey, Member, Role
from gradeflow_app.requests import Request

KEY_NOT_SHARED_YET = "You'll see student IDs once another member of this course opens GradeFlow."
KEY_LOST = "No member holds this course's key any more, so its student IDs can't be read."


class CourseKeyForm(forms.ModelForm[CourseKey]):
    class Meta:
        model = CourseKey
        fields = ("encrypted_key",)


class ShareCourseKeyForm(CourseKeyForm):
    public_key = forms.CharField()
    """The public key the course key was encrypted for. Refused if the member's has changed since."""


@dataclass(frozen=True)
class MemberCourseKey:
    encrypted_key: str = ""
    unavailable_reason: str = ""


def member_course_key(member: Member) -> MemberCourseKey:
    """Both fields are empty when the course has no key yet."""
    encrypted_key = CourseKey.objects.filter(member=member).values_list("encrypted_key", flat=True).first()
    if encrypted_key:
        return MemberCourseKey(encrypted_key=encrypted_key)
    if _any_member_has_key(member.course_id):
        return MemberCourseKey(unavailable_reason=KEY_NOT_SHARED_YET)
    if _has_encrypted_student_ids(member.course_id):
        return MemberCourseKey(unavailable_reason=KEY_LOST)
    return MemberCourseKey()


def _any_member_has_key(course_id: UUID) -> bool:
    return CourseKey.objects.filter(member__course=course_id).exists()


def _has_encrypted_student_ids(course_id: UUID) -> bool:
    return Course.objects.filter(pk=course_id, assessments__students__isnull=False).exists()


@require_POST
@course_member(Role.EDITOR)
def create_course_key(request: Request, member: Member) -> HttpResponse:
    """409 if the course already has a key, or student IDs encrypted with a lost one."""
    form = CourseKeyForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    with transaction.atomic():
        Course.objects.select_for_update().get(pk=member.course_id)
        if _any_member_has_key(member.course_id) or _has_encrypted_student_ids(member.course_id):
            return HttpResponse(status=409)
        CourseKey.objects.create(member=member, encrypted_key=form.cleaned_data["encrypted_key"])
    return HttpResponse(status=204)


@require_POST
@course_member()
def share_course_key(request: Request, member: Member, user_id: UUID) -> HttpResponse:
    form = ShareCourseKeyForm(request.POST)
    if not form.is_valid():
        return HttpResponse(status=400)
    if not CourseKey.objects.filter(member=member).exists():
        raise Http404
    recipient = get_object_or_404(
        Member, course=member.course_id, user=user_id, user__public_key=form.cleaned_data["public_key"]
    )
    CourseKey.objects.get_or_create(member=recipient, defaults={"encrypted_key": form.cleaned_data["encrypted_key"]})
    return HttpResponse(status=204)


def pending_course_key_shares(user: User) -> list[dict[str, str]]:
    """Members without the key of a course `user` has the key of, with `user`'s encrypted copy to share from."""
    keys_by_course = dict(CourseKey.objects.filter(member__user=user).values_list("member__course_id", "encrypted_key"))
    members_without_key = (
        Member.objects.filter(course__in=keys_by_course, key=None)
        .exclude(user__public_key="")
        .values_list("course_id", "user_id", "user__public_key")
    )
    return [
        {
            "courseId": str(course_id),
            "userId": str(user_id),
            "publicKey": public_key,
            "encryptedCourseKey": keys_by_course[course_id],
        }
        for course_id, user_id, public_key in members_without_key
    ]


@receiver(password_reset)
def _delete_course_keys_on_password_reset(request: HttpRequest, user: User, **kwargs: Any) -> None:
    """A reset replaces the key pair, so the keys encrypted for the old one can't be decrypted."""
    CourseKey.objects.filter(member__user=user).delete()
