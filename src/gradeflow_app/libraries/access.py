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
        return Member.objects.filter(course=library.course_id, user=user, role__in=Role.EDITOR.and_above()).exists()
    return library.user_id == user.pk


def usable_methods_filter(user: User, prefix: str = "") -> Q:
    """The methods `user` may use, or what leads to them through the relation `prefix`: their own and their courses',
    and public ones."""
    return (
        Q(**{f"{prefix}library__user": user})
        | Q(**{f"{prefix}library__course__members__user": user})
        | Q(**{f"{prefix}public": True})
    )


def openable_methods_filter(user: User) -> Q:
    """The methods `user` can open: those they may use, and those their courses use."""
    return usable_methods_filter(user) | Q(versions__markings__assessment__course__members__user=user)


@dataclass(frozen=True)
class MethodAccess:
    """Someone looking at a method: whether they can change it, and their membership of its course, if any."""

    method: Method
    can_edit: bool
    member: Member | None

    @property
    def layout(self) -> str:
        """A course's method shows in the course; any other on its own."""
        return "layouts/course.html" if self.member else "layouts/methods.html"


def method_viewer(*, edit: bool = False) -> Callable[[View], View]:
    """Calls the view with a `MethodAccess` of the method in place of the URL's `method` ID. Someone who can't open the
    method gets "not found"; with `edit`, someone who can't change it gets "forbidden"."""

    def decorate(view: View) -> View:
        @wraps(view)
        def wrapped(request: Request, method: UUID, *args: object, **kwargs: object) -> HttpResponse:
            methods = Method.objects.filter(openable_methods_filter(request.user)).distinct()
            found_method = get_object_or_404(methods.select_related("library__course"), pk=method)
            editable = can_edit(request.user, found_method.library)
            if edit and not editable:
                raise PermissionDenied
            member = Member.objects.filter(course=found_method.library.course_id, user=request.user).first()
            return view(request, MethodAccess(found_method, editable, member), *args, **kwargs)

        return wrapped

    return decorate
