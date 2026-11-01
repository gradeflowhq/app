from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from uuid import UUID

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Member, Role
from gradeflow_app.libraries.models import Library, Method
from gradeflow_app.requests import Request, View


def can_edit(user: User, library: Library) -> bool:
    """Whether `user` saves, archives and deletes the library's methods: its owner, or its course's editors."""
    if library.course_id is not None:
        return Member.objects.filter(course=library.course_id, user=user, role__in=Role.EDITOR.at_least()).exists()
    return library.user_id == user.pk


def reachable(user: User, through: str = "") -> Q:
    """The methods `user` may use, or what leads to them `through` a relation: their own and their courses', and
    public ones."""
    return (
        Q(**{f"{through}library__user": user})
        | Q(**{f"{through}library__course__members__user": user})
        | Q(**{f"{through}public": True})
    )


def visible(user: User) -> Q:
    """The methods `user` can open: those they may use, and those their courses use."""
    return reachable(user) | Q(versions__markings__assessment__course__members__user=user)


@dataclass(frozen=True)
class Viewer:
    """Someone looking at a method: whether they can change it, and their membership of its course, if any."""

    method: Method
    can_edit: bool
    member: Member | None

    @property
    def layout(self) -> str:
        """A course's method shows in the course; any other on its own."""
        return "layouts/course.html" if self.member else "layouts/methods.html"


def method_viewer(*, edit: bool = False) -> Callable[[View], View]:
    """Calls the view with a `Viewer` of the method in place of the URL's `method` ID. Someone who can't open the
    method gets "not found"; with `edit`, someone who can't change it gets "forbidden"."""

    def decorate(view: View) -> View:
        @wraps(view)
        def wrapped(request: Request, method: UUID, *args: object, **kwargs: object) -> HttpResponse:
            methods = Method.objects.filter(visible(request.user)).distinct().select_related("library__course")
            found = get_object_or_404(methods, pk=method)
            editable = can_edit(request.user, found.library)
            if edit and not editable:
                raise PermissionDenied
            member = Member.objects.filter(course=found.library.course_id, user=request.user).first()
            return view(request, Viewer(found, editable, member), *args, **kwargs)

        return wrapped

    return decorate
