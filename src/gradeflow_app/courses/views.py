from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlencode

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
from gradeflow_app.courses.invitations import accept_all, invitation_of, send, token
from gradeflow_app.courses.models import INVITATION_LIFETIME, Course, Invitation, Member, Role
from gradeflow_app.dashboard.progress import Stage, progress_of
from gradeflow_app.libraries.models import Library
from gradeflow_app.libraries.services import follow, free_namespace, retire
from gradeflow_app.live.topics import publish, topic
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import is_fragment

SEARCH_FROM = 9
"""How many courses someone has before their list offers a search."""


@dataclass(frozen=True)
class CourseCard:
    """A course in someone's list, with how many of its assessments need what."""

    member: Member
    assessments: int
    to_mark: int
    to_send: int
    sent: int


def _card(member: Member) -> CourseCard:
    stages = Counter(progress.stage for _, progress in progress_of(member.course.assessments.all()))
    return CourseCard(
        member,
        assessments=stages.total(),
        to_mark=stages[Stage.IMPORT] + stages[Stage.MARK],
        to_send=stages[Stage.SEND],
        sent=stages[Stage.SENT],
    )


def courses(request: Request) -> HttpResponse:
    form = CourseForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            course = form.save()
            Library.objects.create(namespace=free_namespace(course.name), course=course)
            Member.objects.create(course=course, user=request.user, role=Role.OWNER)
        return redirect("course", course.pk)
    members = Member.objects.filter(user=request.user).select_related("course").order_by("-course__updated_at")
    searchable = members.count() >= SEARCH_FROM
    query = request.GET.get("q", "").strip()
    if query:
        members = members.filter(course__name__icontains=query)
    context = {
        "cards": [_card(member) for member in members],
        "mixed_roles": len({member.role for member in members}) > 1,
        "searchable": searchable or query,
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
    rows = progress_of(
        member.course.assessments.annotate(student_count=Count("students", distinct=True)).order_by("-updated_at")
    )
    return render(request, "courses/course.html", {"member": member, "rows": rows, "form": form})


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
            publish(topic("members", member.course_id))
            form = InviteForm()
        context = _members(member, form) | {"oob": True}
        return render(request, "courses/members.html#invite", context, status=200 if valid else 422)
    if is_fragment(request):
        return _members_partial(request, member)
    return render(request, "courses/members.html", _members(member, InviteForm()))


def _members(member: Member, form: InviteForm) -> dict[str, object]:
    members = list(member.course.members.select_related("user"))
    return {
        "member": member,
        "members": members,
        "sole_owner": sum(person.is_owner for person in members) == 1,
        "invitations": member.course.invitations.filter(accepted_at=None),
        "form": form,
        "roles": Role.choices,
    }


def _members_partial(request: Request, member: Member) -> HttpResponse:
    return render(request, "courses/members.html#members", _members(member, InviteForm()))


def _invite(request: Request, course: Course, email: str, role: Role) -> None:
    user = User.objects.filter(email__iexact=email).first()
    if user is not None:
        _, created = Member.objects.get_or_create(course=course, user=user, defaults={"role": role})
        if not created:
            messages.info(request, f"{user} is already a member.")
            return
        url = request.build_absolute_uri(reverse("course", args=[course.pk]))
        send("shared", user.email, {"inviter": request.user, "course": course, "role": role.label, "url": url})
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
    url = request.build_absolute_uri(reverse("invitation", args=[token(invitation)]))
    context = {"inviter": request.user, "course": invitation.course, "role": invitation.get_role_display()}
    send("invitation", invitation.email, context | {"url": url, "lifetime": INVITATION_LIFETIME})


def _made(request: Request, who: object, role: Role) -> None:
    messages.success(request, f"{who}'s role is now {role.label}.")


@require_POST
@transaction.atomic
@course_member(Role.OWNER)
def member_role(request: Request, member: Member, user: str) -> HttpResponse:
    target = get_object_or_404(Member, course=member.course, user=user)
    form = RoleForm(request.POST)
    if not form.is_valid():
        return _members_partial(request, member)
    if _keeps_an_owner(request, target, form.cleaned_data["role"]):
        target.role = form.cleaned_data["role"]
        target.save(update_fields=["role"])
        publish(topic("members", member.course_id))
        _made(request, target.user, form.cleaned_data["role"])
        if target == member:
            return HttpResponseClientRefresh()
    return _members_partial(request, member)


@require_POST
@transaction.atomic
@course_member(Role.OWNER)
def member_remove(request: Request, member: Member, user: str) -> HttpResponse:
    target = get_object_or_404(Member, course=member.course, user=user)
    if _removed(request, target):
        messages.success(request, f"Removed {target.user}.")
    return _members_partial(request, member)


@require_POST
@course_member(Role.OWNER)
def invitation_revoke(request: Request, member: Member, invitation_id: str) -> HttpResponse:
    invitation = get_object_or_404(Invitation, course=member.course, pk=invitation_id)
    invitation.delete()
    messages.success(request, f"Revoked the invitation to {invitation.email}.")
    publish(topic("members", member.course_id))
    return _members_partial(request, member)


@require_POST
@course_member(Role.OWNER)
def invitation_resend(request: Request, member: Member, invitation_id: str) -> HttpResponse:
    invitation = get_object_or_404(Invitation, course=member.course, pk=invitation_id)
    invitation.sent_at = timezone.now()
    invitation.save(update_fields=["sent_at"])
    _send(request, invitation)
    messages.success(request, f"Sent the invitation to {invitation.email} again.")
    publish(topic("members", member.course_id))
    return _members_partial(request, member)


@require_POST
@course_member(Role.OWNER)
def invitation_role(request: Request, member: Member, invitation_id: str) -> HttpResponse:
    invitation = get_object_or_404(Invitation, course=member.course, pk=invitation_id)
    form = RoleForm(request.POST)
    if form.is_valid():
        invitation.role = form.cleaned_data["role"]
        invitation.save(update_fields=["role"])
        publish(topic("members", member.course_id))
        _made(request, invitation.email, form.cleaned_data["role"])
    return _members_partial(request, member)


def _keeps_an_owner(request: Request, target: Member, role: Role | None) -> bool:
    """Whether `target` can change to `role`, or leave the course if it's None, and still leave it an owner. Says
    why not in a message. Locks the course's owners, so owners changing each other at once can't all go."""
    owners = {owner.pk for owner in Member.objects.select_for_update().filter(course=target.course, role=Role.OWNER)}
    if target.pk in owners and role != Role.OWNER and not owners - {target.pk}:
        messages.error(request, "A course needs an owner. Make someone else an owner first.")
        return False
    return True


def _removed(request: Request, target: Member) -> bool:
    """Removes `target` from the course, unless that leaves it without an owner."""
    if not _keeps_an_owner(request, target, None):
        return False
    target.delete()
    publish(topic("members", target.course_id))
    return True


@require_POST
@transaction.atomic
@course_member()
def leave_course(request: Request, member: Member) -> HttpResponse:
    if not _removed(request, member):
        return _members_partial(request, member)
    messages.success(request, f"Left “{member.course.name}”.")
    return HttpResponseClientRedirect(reverse("courses"))


@login_not_required
def invitation(request: HttpRequest, value: str) -> HttpResponse:
    invitation = invitation_of(value)
    if invitation is None:
        return render(request, "courses/invitation_invalid.html", status=404)
    if request.user.is_authenticated and invitation.course.members.filter(user=request.user).exists():
        return redirect("course", invitation.course_id)
    if invitation.expired or invitation.accepted_at:
        return render(request, "courses/invitation_invalid.html", status=404)
    if not request.user.is_authenticated:
        known = User.objects.filter(email__iexact=invitation.email).exists()
        target = reverse("account_login" if known else "account_signup")
        return redirect(f"{target}?{urlencode({'email': invitation.email, 'next': request.path})}")
    if request.user.email.lower() != invitation.email:
        return render(request, "courses/invitation_invalid.html", {"other": invitation.email}, status=403)
    accept_all(request.user, invitation.email)
    return redirect("course", invitation.course_id)


@course_member(Role.OWNER)
def course_settings(request: Request, member: Member) -> HttpResponse:
    form = CourseForm(request.POST or None, instance=member.course)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            course = form.save()
            if "name" in form.changed_data:
                follow(course.library, course.name)
        messages.success(request, "Saved.")
        return redirect("course_settings", member.course_id)
    return render(request, "courses/settings.html", {"member": member, "form": form})


@require_POST
@course_member(Role.OWNER)
@transaction.atomic
def delete_course(request: Request, member: Member) -> HttpResponse:
    name, library = member.course.name, member.course.library
    member.course.delete()
    retire(library)
    messages.success(request, f"Deleted “{name}”.")
    return redirect("courses")
