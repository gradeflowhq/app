"""The methods a course marks with: the core's, and saved versions, which never change.

A course's markings load the versions they use. A person editing marking may use their own methods, their
courses', public ones and those the course uses; the picker offers the latest version of each that isn't archived.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache, cached_property, lru_cache
from uuid import UUID

from django.db.models import Q, QuerySet
from gradeflow_core import BUILTINS, Methods
from gradeflow_core import Method as CoreMethod
from gradeflow_core.editing import LoadedFiles, load_method_files

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.models import Course
from gradeflow_app.libraries.access import usable_methods_filter
from gradeflow_app.libraries.models import Method, MethodVersion

type MethodFiles = tuple[tuple[str, str], ...]
"""Published versions as the core loads them: each method's name and YAML."""

BUILTIN_NAMES = frozenset(method.name for method in BUILTINS)
BUILTIN_REFS = frozenset(method.ref for method in BUILTINS)
_LOG = logging.getLogger(__name__)


def method_files(versions: QuerySet[MethodVersion]) -> MethodFiles:
    rows = versions.select_related("method__library").order_by("method__library__namespace", "method__name", "number")
    return tuple((version.method.full_name, version.definition) for version in rows)


@lru_cache(maxsize=64)
def load_saved(published: MethodFiles) -> LoadedFiles:
    """Versions never change, so loading them once is enough."""
    loaded = load_method_files(published, BUILTINS)
    for ref, failed in loaded.failed.items():
        _log_failure(ref, failed.ref, " ".join(map(str, failed.problems)))
    return loaded


@cache
def _log_failure(ref: str, at_fault: str, problems: str) -> None:
    """Logs a saved version this core can't load, once: it won't change."""
    _LOG.warning("Saved method %s doesn't load, for problems in %s: %s", ref, at_fault, problems)


def load_methods(published: MethodFiles) -> Methods:
    return load_saved(published).methods


def files_needed_by(refs: Iterable[str]) -> MethodFiles:
    """The saved versions marking with `refs` loads: those it names, and those they use in turn."""
    return method_files(MethodVersion.objects.named(refs).with_used_versions())


def loaded_course_methods(course: Course) -> LoadedFiles:
    """The core's methods and every version the course's markings use."""
    return load_saved(
        method_files(MethodVersion.objects.filter(markings__assessment__course=course).with_used_versions())
    )


def course_methods(course: Course) -> Methods:
    return loaded_course_methods(course).methods


def loaded_usable_methods(user: User, course: Course | None) -> LoadedFiles:
    """Every version `user` may mark with in `course`, archived ones too: their own and their courses' methods,
    public ones, those the course uses, and what those use in turn."""
    query = usable_methods_filter(user, "method__")
    if course is not None:
        query |= Q(markings__assessment__course=course)
    return load_saved(method_files(MethodVersion.objects.filter(query).with_used_versions()))


def usable(user: User, course: Course | None) -> Methods:
    return loaded_usable_methods(user, course).methods


@dataclass(frozen=True)
class OfferedMethods:
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

    def newer_version(self, method: CoreMethod, methods: Methods) -> CoreMethod | None:
        """The latest version of `method` among `methods`, if it is newer and still offered."""
        if method.name not in self.names | BUILTIN_NAMES:
            return None
        latest = next((each for each in methods.latest() if each.name == method.name), None)
        return latest if latest is not None and latest.version > method.version else None


def offers(user: User, course: Course | None) -> OfferedMethods:
    """The methods `user` can choose from in `course`: those it uses, its own, theirs, their other courses', then
    public ones."""
    query = usable_methods_filter(user)
    used: set[UUID] = set()
    if course is not None:
        used = set(MethodVersion.objects.filter(markings__assessment__course=course).values_list("method", flat=True))
        query |= Q(pk__in=used)
    candidates = (
        Method.objects.filter(query, archived=False, versions__isnull=False)
        .distinct()
        .select_related("library__course", "library__user")
    )
    course_ids = set(user.memberships.values_list("course", flat=True))
    names_by_section: dict[tuple[int, str, UUID | None], list[str]] = {}  # by section, in order, then name
    public: list[str] = []
    owners: dict[str, str] = {}
    for method in sorted(candidates, key=lambda each: each.full_name):
        library = method.library
        owners[method.full_name] = library.owner_name
        section: tuple[int, str, UUID | None]
        if method.pk in used:
            section = (0, "Used in this course", None)
        elif course is not None and library.course_id == course.pk:
            section = (1, course.name, course.pk)
        elif library.user_id == user.pk:
            section = (2, "Your methods", None)
        elif library.course is not None and library.course_id in course_ids:
            section = (3, library.course.name, library.course_id)
        else:
            public.append(method.full_name)
            continue
        names_by_section.setdefault(section, []).append(method.full_name)
    sections = tuple(
        (label, tuple(names)) for (_, label, _), names in sorted(names_by_section.items(), key=lambda item: item[0][:2])
    )
    archived = Method.objects.filter(pk__in=used, archived=True).select_related("library")
    return OfferedMethods(sections, tuple(public), owners, frozenset(method.full_name for method in archived))
