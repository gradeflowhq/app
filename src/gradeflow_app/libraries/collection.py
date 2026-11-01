"""The methods a course marks with: the core's, and saved versions, which never change.

A course's markings load the versions they use. A person editing marking may use their own methods, their
courses', public ones and those the course uses; the picker offers the latest version of each that isn't archived.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property, lru_cache
from typing import Any
from uuid import UUID

from django.db.models import Q, QuerySet
from gradeflow_core import BUILTINS, Methods
from gradeflow_core import Method as CoreMethod

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course
from gradeflow_app.libraries.access import reachable
from gradeflow_app.libraries.models import Method, MethodVersion

type Files = tuple[tuple[str, str], ...]
"""Published versions as the core loads them: each method's name and YAML."""

BUILTIN_NAMES = frozenset(method.name for method in BUILTINS)
BUILTIN_REFS = frozenset(method.ref for method in BUILTINS)


def files(versions: QuerySet[MethodVersion]) -> Files:
    rows = versions.select_related("method__library").order_by("method__library__namespace", "method__name", "number")
    return tuple((version.method.full_name, version.definition) for version in rows)


@lru_cache(maxsize=64)
def loaded(published: Files) -> Methods:
    """Versions never change, so loading them once is enough."""
    return BUILTINS.with_files(published)


def needed(refs: Iterable[str]) -> Files:
    """The saved versions marking with `refs` loads: those it names, and those they use in turn."""
    return files(MethodVersion.objects.named(refs).with_uses())


def in_use(course: Course) -> Methods:
    """The core's methods and every version the course's markings use."""
    return loaded(files(MethodVersion.objects.filter(markings__assessment__course=course).with_uses()))


def usable(user: User, course: Course | None) -> Methods:
    """Every version `user` may mark with in `course`, archived ones too: their own and their courses' methods,
    public ones, those the course uses, and what those use in turn."""
    query = reachable(user, "method__")
    if course is not None:
        query |= Q(markings__assessment__course=course)
    return loaded(files(MethodVersion.objects.filter(query).with_uses()))


@dataclass(frozen=True)
class Offers:
    """The saved methods to offer, by name: the person's, in sections, each in the first one it fits, then other
    people's public ones."""

    sections: tuple[tuple[str, tuple[str, ...]], ...]
    public: tuple[str, ...]
    owners: dict[str, str]
    """Who made each: "Introductory Biology"."""
    archived: frozenset[str] = frozenset()
    """The archived methods the course uses, which are offered nowhere."""

    @cached_property
    def names(self) -> frozenset[str]:
        return frozenset(name for _, names in self.sections for name in names) | frozenset(self.public)

    def newer(self, method: CoreMethod[Any], methods: Methods) -> CoreMethod[Any] | None:
        """The latest version of `method` among `methods`, if it is newer and still offered."""
        if method.name not in self.names | BUILTIN_NAMES:
            return None
        found = next((item for item in methods.latest() if item.name == method.name), None)
        return found if found is not None and found.version > method.version else None


def offers(user: User, course: Course | None) -> Offers:
    """The methods `user` can choose from in `course`: those it uses, its own, theirs, their other courses', then
    public ones."""
    query = reachable(user)
    used: set[UUID] = set()
    if course is not None:
        used = set(MethodVersion.objects.filter(markings__assessment__course=course).values_list("method", flat=True))
        query |= Q(pk__in=used)
    candidates = (
        Method.objects.filter(query, archived=False, versions__isnull=False)
        .distinct()
        .select_related("library__course", "library__user")
    )
    courses = set(user.memberships.values_list("course", flat=True))
    found: dict[tuple[int, str, UUID | None], list[str]] = {}  # by section, in order, then name
    public: list[str] = []
    owners: dict[str, str] = {}
    for method in sorted(candidates, key=lambda each: each.full_name):
        library = method.library
        owners[method.full_name] = library.owner
        key: tuple[int, str, UUID | None]
        if method.pk in used:
            key = (0, "Used in this course", None)
        elif course is not None and library.course_id == course.pk:
            key = (1, course.name, course.pk)
        elif library.user_id == user.pk:
            key = (2, "Your methods", None)
        elif library.course is not None and library.course_id in courses:
            key = (3, library.course.name, library.course_id)
        else:
            public.append(method.full_name)
            continue
        found.setdefault(key, []).append(method.full_name)
    sections = tuple(
        (label, tuple(names)) for (_, label, _), names in sorted(found.items(), key=lambda item: item[0][:2])
    )
    archived = Method.objects.filter(pk__in=used, archived=True).select_related("library")
    return Offers(sections, tuple(public), owners, frozenset(method.full_name for method in archived))
