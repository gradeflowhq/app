from collections.abc import Callable
from functools import wraps
from typing import Any
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse

from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.requests import Request, View


def member_of(request: Request, course_id: UUID, role: Role = Role.VIEWER) -> Member:
    """The user's membership of the course. Someone outside it gets "not found", so they can't tell it exists; a
    member whose role is below `role` gets "forbidden"."""
    member = Member.objects.select_related("course").filter(course=course_id, user=request.user).first()
    if member is None:
        raise Http404
    if not Role(member.role).allows(role):
        raise PermissionDenied
    return member


def course_of(request: Request) -> Course | None:
    """The course the query's `course` names, if the user is in it."""
    try:
        return Course.objects.filter(pk=UUID(request.GET["course"]), members__user=request.user).first()
    except KeyError, ValueError:
        return None


def course_member(role: Role = Role.VIEWER) -> Callable[[View], View]:
    """Calls the view with the user's `Member` of the course in place of the URL's `course` ID, once their role is
    at least `role`."""
    return passing("course", member_of, role)


def passing(argument: str, find: Callable[[Request, UUID, Role], object], role: Role) -> Callable[[View], View]:
    """Calls the view with the user's access to what the URL's `argument` ID names, as `find` gives it for `role`, in
    place of the ID."""

    def decorate(view: View) -> View:
        @wraps(view)
        def wrapped(request: Request, *args: object, **kwargs: Any) -> HttpResponse:
            return view(request, find(request, kwargs.pop(argument), role), *args, **kwargs)

        return wrapped

    return decorate
