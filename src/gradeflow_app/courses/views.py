from collections import Counter
from dataclasses import dataclass

from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseClientRedirect, HttpResponseClientRefresh

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.forms import AssessmentForm
from gradeflow_app.courses.access import course_member
from gradeflow_app.courses.forms import CourseForm, InviteForm, RoleForm
from gradeflow_app.courses.invitations import accept_invitations, invitation_from_token, invitation_token, send_email
from gradeflow_app.courses.models import INVITATION_LIFETIME, Course, Invitation, Member, Role
from gradeflow_app.dashboard.progress import Stage, progress_of
from gradeflow_app.libraries.models import Library
from gradeflow_app.libraries.services import available_namespace, close_library, rename_namespace
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import is_fragment

MIN_COURSES_FOR_SEARCH = 9
"""How many courses someone has before their list offers a search."""


@dataclass(frozen=True)
class CourseCard:
    """A course in someone's list, with how many of its assessments need what."""

    member: Member
    assessment_count: int
    to_mark_count: int
    to_send_count: int
    sent_count: int


def _course_card(member: Member) -> CourseCard:
    stage_counts = Counter(progress.stage for _, progress in progress_of(member.course.assessments.all()))
    return CourseCard(
        member,
        assessment_count=stage_counts.total(),
        to_mark_count=stage_counts[Stage.IMPORT] + stage_counts[Stage.MARK],
        to_send_count=stage_counts[Stage.SEND],
        sent_count=stage_counts[Stage.SENT],
    )


def courses(request: Request) -> HttpResponse:
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            course = form.save()
            Library.objects.create(namespace=available_namespace(course.name), course=course)
            Member.objects.create(course=course, user=request.user, role=Role.OWNER)
        return redirect("course", course.pk)
    members = Member.objects.filter(user=request.user).select_related("course").order_by("-course__updated_at")
    offers_search = members.count() >= MIN_COURSES_FOR_SEARCH
    query = request.GET.get("q", "").strip()
    if query:
        members = members.filter(course__name__icontains=query)
    context = {
        "cards": [_course_card(member) for member in members],
        "has_mixed_roles": len({member.role for member in members}) > 1,
        "offers_search": offers_search or query,
        "form": form,
        "query": query,
    }
    return render(request, "courses/list.html", context)


@course_member()
def course(request: Request, member: Member) -> HttpResponse:
    form = AssessmentForm(request.POST or None)
    if request.method == "POST" and not member.can_edit:
        raise PermissionDenied
    if request.method == "POST" and form.is_valid():
        assessment = form.save(commit=False)
        assessment.course = member.course
        assessment.save()
        return redirect("import", assessment.pk)
    assessment_progress = progress_of(
        member.course.assessments.annotate(student_count=Count("students", distinct=True)).order_by("-updated_at")
    )
    context = {"member": member, "assessment_progress": assessment_progress, "form": form}
    return render(request, "courses/course.html", context)


@course_member()
def members(request: Request, member: Member) -> HttpResponse:
    """The course's members and invitations; posting invites someone, answered with the invite dialog's form and
    the list. Live refreshes get the list alone."""
    if request.method == "POST":
        if not member.is_owner:
            raise PermissionDenied
        form = InviteForm(request.POST)
        valid = form.is_valid()
        if valid:
            _invite(request, member.course, form.cleaned_data["email"], form.cleaned_data["role"])
            notify_change(topic("members", member.course_id))
            form = InviteForm()
        context = _members_context(member, form) | {"out_of_band": True}
        return render(request, "courses/members.html#invite", context, status=200 if valid else 422)
    if is_fragment(request):
        return _members_partial(request, member)
    return render(request, "courses/members.html", _members_context(member, InviteForm()))


def _members_context(member: Member, form: InviteForm) -> dict[str, object]:
    members = list(member.course.members.select_related("user"))
    return {
        "member": member,
        "members": members,
        "has_one_owner": sum(person.is_owner for person in members) == 1,
        "invitations": member.course.invitations.filter(accepted_at=None),
        "form": form,
        "roles": Role.choices,
    }


def _members_partial(request: Request, member: Member) -> HttpResponse:
    return render(request, "courses/members.html#members", _members_context(member, InviteForm()))


def _invite(request: Request, course: Course, email: str, role: Role) -> None:
    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
        _, created = Member.objects.get_or_create(course=course, user=user, defaults={"role": role})
        if not created:
            messages.info(request, f"{user} is already a member.")
            return
        url = request.build_absolute_uri(reverse("course", args=[course.pk]))
        send_email(
            "added_to_course", user.email, {"inviter": request.user, "course": course, "role": role.label, "url": url}
        )
        messages.success(request, f"Added {user}.")
        return
    invitation, _ = Invitation.objects.update_or_create(
        course=course,
        email=email.lower(),
        defaults={"role": role, "sent_at": timezone.now()},
    )
    _send(request, invitation)
    messages.success(request, f"Invited {invitation.email}.")


def _send(request: Request, invitation: Invitation) -> None:
    url = request.build_absolute_uri(reverse("invitation", args=[invitation_token(invitation)]))
    context = {"inviter": request.user, "course": invitation.course, "role": invitation.get_role_display()}
    send_email("invitation", invitation.email, context | {"url": url, "lifetime": INVITATION_LIFETIME})


@require_POST
@transaction.atomic
@course_member(Role.OWNER)
def member_role(request: Request, member: Member, user_id: str) -> HttpResponse:
    target = get_object_or_404(Member, course=member.course, user=user_id)
    form = RoleForm(request.POST)
    if not form.is_valid():
        return _members_partial(request, member)
    if _leaves_an_owner(request, target, form.cleaned_data["role"]):
        target.role = form.cleaned_data["role"]
        target.save(update_fields=["role"])
        notify_change(topic("members", member.course_id))
        messages.success(request, f"{target.user}'s role is now {form.cleaned_data['role'].label}.")
        if target == member:
            return HttpResponseClientRefresh()
    return _members_partial(request, member)


@require_POST
@transaction.atomic
@course_member(Role.OWNER)
def member_remove(request: Request, member: Member, user_id: str) -> HttpResponse:
    target = get_object_or_404(Member, course=member.course, user=user_id)
    if _remove_member(request, target):
        messages.success(request, f"Removed {target.user}.")
    return _members_partial(request, member)


@require_POST
@course_member(Role.OWNER)
def invitation_revoke(request: Request, member: Member, invitation_id: str) -> HttpResponse:
    invitation = get_object_or_404(Invitation, course=member.course, pk=invitation_id)
    invitation.delete()
    messages.success(request, f"Revoked the invitation to {invitation.email}.")
    notify_change(topic("members", member.course_id))
    return _members_partial(request, member)


@require_POST
@course_member(Role.OWNER)
def invitation_resend(request: Request, member: Member, invitation_id: str) -> HttpResponse:
    invitation = get_object_or_404(Invitation, course=member.course, pk=invitation_id)
    invitation.sent_at = timezone.now()
    invitation.save(update_fields=["sent_at"])
    _send(request, invitation)
    messages.success(request, f"Sent the invitation to {invitation.email} again.")
    notify_change(topic("members", member.course_id))
    return _members_partial(request, member)


@require_POST
@course_member(Role.OWNER)
def invitation_role(request: Request, member: Member, invitation_id: str) -> HttpResponse:
    invitation = get_object_or_404(Invitation, course=member.course, pk=invitation_id)
    form = RoleForm(request.POST)
    if form.is_valid():
        invitation.role = form.cleaned_data["role"]
        invitation.save(update_fields=["role"])
        notify_change(topic("members", member.course_id))
        messages.success(request, f"{invitation.email}'s role is now {form.cleaned_data['role'].label}.")
    return _members_partial(request, member)


def _leaves_an_owner(request: Request, target: Member, role: Role | None) -> bool:
    """Whether `target` can change to `role`, or leave the course if it's None, and still leave it an owner. Says
    why not in a message. Locks the course's owners, so owners changing each other at once can't all go."""
    owners = {owner.pk for owner in Member.objects.select_for_update().filter(course=target.course, role=Role.OWNER)}
    if target.pk in owners and role != Role.OWNER and not owners - {target.pk}:
        messages.error(request, "A course needs an owner. Make someone else an owner first.")
        return False
    return True


def _remove_member(request: Request, target: Member) -> bool:
    """Removes `target` from the course, unless that leaves it without an owner."""
    if not _leaves_an_owner(request, target, None):
        return False
    target.delete()
    notify_change(topic("members", target.course_id))
    return True


@require_POST
@transaction.atomic
@course_member()
def leave_course(request: Request, member: Member) -> HttpResponse:
    if not _remove_member(request, member):
        return _members_partial(request, member)
    messages.success(request, f"Left “{member.course.name}”.")
    return HttpResponseClientRedirect(reverse("courses"))


@login_not_required
def invitation(request: HttpRequest, token: str) -> HttpResponse:
    invitation = invitation_from_token(token)
    if invitation is None:
        return render(request, "courses/invitation_invalid.html", status=404)
    if request.user.is_authenticated and invitation.course.members.filter(user=request.user).exists():
        return redirect("course", invitation.course_id)
    if invitation.is_expired or invitation.accepted_at:
        return render(request, "courses/invitation_invalid.html", status=404)
    if not request.user.is_authenticated:
        has_account = User.objects.filter(email__iexact=invitation.email).exists()
        query = {"email": invitation.email, "next": request.path}
        return redirect(reverse("account_login" if has_account else "account_signup", query=query))
    if request.user.email.lower() != invitation.email:
        return render(request, "courses/invitation_invalid.html", {"invited_email": invitation.email}, status=403)
    accept_invitations(request.user, invitation.email)
    return redirect("course", invitation.course_id)


@course_member(Role.OWNER)
def course_settings(request: Request, member: Member) -> HttpResponse:
    form = CourseForm(request.POST or None, instance=member.course)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            course = form.save()
            if "name" in form.changed_data:
                rename_namespace(course.library, course.name)
        messages.success(request, "Saved.")
        return redirect("course_settings", member.course_id)
    return render(request, "courses/settings.html", {"member": member, "form": form})


@require_POST
@course_member(Role.OWNER)
@transaction.atomic
def delete_course(request: Request, member: Member) -> HttpResponse:
    name, library = member.course.name, member.course.library
    member.course.delete()
    close_library(library)
    messages.success(request, f"Deleted “{name}”.")
    return redirect("courses")
