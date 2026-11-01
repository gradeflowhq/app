"""Saving methods to a library: naming libraries and methods, publishing versions, copying a method elsewhere, and
deleting it."""

import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from copy import deepcopy
from itertools import count
from operator import attrgetter
from typing import Any

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet, RestrictedError
from django.template.defaultfilters import pluralize
from gradeflow_core import GradeFlowError, Methods
from gradeflow_core.editing import MethodDraft as CoreDraft
from gradeflow_core.editing import load_yaml, method_name, methods_used

from gradeflow_app.accounts.models import User
from gradeflow_app.libraries.collection import BUILTIN_REFS, files, loaded
from gradeflow_app.libraries.models import (
    NAME_LENGTH,
    NAMESPACE_LENGTH,
    Library,
    Method,
    MethodDraft,
    MethodVersion,
    VersionUse,
)
from gradeflow_app.libraries.workbench import Tests, new_tests, questions_of


def name_from(text: str, limit: int) -> str:
    """`text` as lowercase words joined by hyphens, cut at a hyphen to fit `limit`: `ana-example-edu` from
    `ana@example.edu`. Accents go; other scripts' words go, so it may be empty."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", plain)
    name = words[0][:limit] if words else ""
    for word in words[1:]:
        if len(name) + 1 + len(word) > limit:
            break
        name = f"{name}-{word}"
    return name


def _names(text: str, limit: int, fallback: str) -> Iterator[str]:
    """`text` as a name that fits `limit`, then numbered from 2, cut first so the number fits."""
    name = name_from(text, limit) or fallback
    yield name
    for number in count(2):
        suffix = f"-{number}"
        yield name_from(name, limit - len(suffix)) + suffix


def free_namespace(text: str, *, excluding: Library | None = None) -> str:
    """A namespace made from `text`, an email address or a course's name, that no library but `excluding` has."""
    others = Library.objects.exclude(pk=excluding.pk) if excluding else Library.objects.all()
    return next(
        namespace
        for namespace in _names(text, NAMESPACE_LENGTH, "methods")
        if _allowed(namespace) and not others.filter(namespace=namespace).exists()
    )


def _allowed(namespace: str) -> bool:
    """Whether the core lets a library have `namespace`: not its own."""
    try:
        method_name(namespace, "method")
    except GradeFlowError:
        return False
    return True


def free_name(label: str, library: Library, *, excluding: Method | None = None) -> str:
    """A name made from `label` that no method of the library but `excluding` has."""
    others = library.methods.exclude(pk=excluding.pk) if excluding else library.methods.all()
    taken = set(others.values_list("name", flat=True))
    return next(name for name in _names(label, NAME_LENGTH, "method") if name not in taken)


def unused(methods: QuerySet[Method]) -> bool:
    """Whether no saved marking and no method's version uses a version of `methods`. Locks the versions, so a
    marking or version linking one at the same moment has committed and counts, or waits until this commits."""
    versions = MethodVersion.objects.filter(method__in=methods)
    list(versions.select_for_update())
    return not (
        versions.filter(markings__isnull=False).exists() or VersionUse.objects.filter(used__in=versions).exists()
    )


@transaction.atomic
def follow(library: Library, text: str) -> None:
    """Make the namespace again from `text`, its owner's new name or email address, unless its methods are used."""
    library = Library.objects.select_for_update().get(pk=library.pk)
    if unused(library.methods.all()):
        library.namespace = free_namespace(text, excluding=library)
        library.save(update_fields=["namespace"])


def taken_label(library: Library, label: str, *, excluding: Method | None = None) -> str | None:
    """What another of the library's methods called `label`, in any case, is called: as published, or as a draft of
    one never published is called."""
    others = library.methods.exclude(pk=excluding.pk) if excluding else library.methods.all()
    names = {method.full_name for method in others}
    versions = MethodVersion.objects.filter(method__library=library).with_uses()
    published = [method.label for method in loaded(files(versions)).latest() if method.name in names]
    first_drafts = MethodDraft.objects.filter(method__in=others, method__versions__isnull=True).select_related(
        "method__library"
    )
    drafted = [draft_of(draft.method, draft.definition, draft.tests, Methods()).label for draft in first_drafts]
    return next((taken for taken in [*published, *drafted] if taken.casefold() == label.casefold()), None)


def _free(library: Library, label: str, *, excluding: Method | None = None) -> None:
    if taken := taken_label(library, label, excluding=excluding):
        raise ValidationError(f"This library already has a method called “{taken}”.")


def draft_of(
    method: Method,
    definition: str,
    tests: Tests,
    methods: Methods,
    *,
    version: int | None = None,
    test_values: Mapping[str, Any] | None = None,
) -> CoreDraft:
    """`definition` as the core reads it as `method`'s `version`, by default its next, with the questions of its
    `tests`."""
    return CoreDraft.from_yaml(
        definition,
        questions_of(tests),
        methods=methods,
        name=method.full_name,
        version=version or method.next_version,
        test_values=test_values,
    )


def start(library: Library, user: User, build: Callable[..., CoreDraft], *, label: str, summary: str) -> Method:
    """A new method called `label` in the library, as `user`'s draft that `build` makes from its name, version,
    label and summary. Raises `ValidationError` if the library has a method called that."""
    _free(library, label)
    name = free_name(label, library)
    core = build(name=method_name(library.namespace, name), version=1, label=label, summary=summary)
    method = Method.objects.create(library=library, name=name, created_by=user)
    MethodDraft.objects.create(
        method=method, user=user, definition=core.to_yaml(), tests=dict(new_tests(core.questions))
    )
    return method


def _link(version: MethodVersion) -> None:
    """Fix the versions `version` needs. Raises `ValidationError` if one is gone."""
    used = {ref for ref in methods_used(load_yaml(version.definition)) if ref != version.ref}
    found = MethodVersion.objects.linked(used)
    if used - {item.ref for item in found} - BUILTIN_REFS:
        raise ValidationError("It uses a method that no longer exists.")
    version.uses.set(found)


def _loaded(version: MethodVersion) -> Methods:
    """The core's methods, `version` and what it uses."""
    return loaded(files(MethodVersion.objects.filter(pk=version.pk).with_uses()))


def label_of(version: MethodVersion) -> str:
    """The name people see for `version`."""
    return draft_of(version.method, version.definition, version.tests, Methods(), version=version.number).label


@transaction.atomic
def publish(draft: MethodDraft, user: User, core: CoreDraft) -> MethodVersion:
    """Publish the draft `core` reads as the method's next version, and end the draft. A new name renames the
    method while nothing uses it. Raises `ValidationError` if the draft can't be published."""
    method = Method.objects.select_for_update().select_related("library").get(pk=draft.method_id)
    previous = method.latest_version
    if previous != draft.based_on:
        raise ValidationError("A newer version was published since you started. Discard this draft to edit it.")
    if problems := core.problems:
        raise ValidationError(str(problems[0]))
    label = core.finalized().label
    _free(method.library, label, excluding=method)
    if (previous is None or label != label_of(previous)) and unused(Method.objects.filter(pk=method.pk)):
        method.name = free_name(label, method.library, excluding=method)
        core.name = method.full_name
    version = MethodVersion.objects.create(
        method=method, number=core.version, definition=core.to_yaml(), tests=draft.tests, created_by=user
    )
    _link(version)
    method.next_version = core.version + 1
    method.save(update_fields=["name", "next_version", "updated_at"])
    draft.delete()
    return version


@transaction.atomic
def copy(version: MethodVersion, library: Library, user: User, label: str) -> Method:
    """`version` as version 1 of a new method called `label` in `library`, with its tests. Raises
    `ValidationError` if the library has a method of that name."""
    _free(library, label)
    method = Method.objects.create(library=library, name=free_name(label, library), next_version=2, created_by=user)
    core = draft_of(method, version.definition, version.tests, _loaded(version), version=1)
    core.update_metadata(label=label)
    copied = MethodVersion.objects.create(
        method=method, number=1, definition=core.to_yaml(), tests=deepcopy(version.tests), created_by=user
    )
    _link(copied)
    return method


def dependents(method: Method, user: User) -> list[str]:
    """What uses a version of `method`: the markings in `user`'s courses, how many others, and other methods."""
    versions = method.versions.prefetch_related("markings__assessment", "markings__question")
    markings = sorted({marking for version in versions for marking in version.markings.all()}, key=attrgetter("pk"))
    courses = set(user.memberships.values_list("course", flat=True))
    mine = [marking for marking in markings if marking.assessment.course_id in courses]
    found = [f"{marking.assessment.name} · {marking}" for marking in mine]
    if others := len(markings) - len(mine):
        found.append(f"{others} other marking{pluralize(others)}")
    users = MethodVersion.objects.filter(
        pk__in=VersionUse.objects.filter(used__method=method).values("version")
    ).exclude(method=method)
    found += [label_of(item) for item in users.select_related("method__library")]
    return found


@transaction.atomic
def delete(method: Method, user: User) -> None:
    """Delete a method nothing uses, with its versions. Raises `ValidationError` naming what uses it."""
    method = Method.objects.select_for_update().get(pk=method.pk)
    list(method.versions.select_for_update())  # waits for what is linking them, so it's found below
    if found := dependents(method, user):
        raise ValidationError(f"Still used by {', '.join(found)}.")
    try:
        with transaction.atomic():
            method.delete()
    except RestrictedError:
        raise ValidationError("It came into use just now.") from None


@transaction.atomic
def retire(library: Library) -> None:
    """After its owner goes: delete the library's methods nothing outside them uses, and make the rest private."""
    methods = set(library.methods.all())
    while True:
        kept = {
            method
            for method in methods
            if method.versions.filter(markings__isnull=False).exists()
            or VersionUse.objects.filter(used__method=method).exclude(version__method__in=methods).exists()
        }
        if not kept:
            break
        methods -= kept
    Method.objects.filter(pk__in=[method.pk for method in methods]).delete()
    library.methods.update(public=False)
