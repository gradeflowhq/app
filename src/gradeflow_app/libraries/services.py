"""Saving methods to a library: naming libraries and methods, publishing versions, copying a method elsewhere, and
deleting it."""

import re
import unicodedata
from collections.abc import Iterator, Mapping
from copy import deepcopy
from itertools import count
from typing import Any, Protocol

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.template.defaultfilters import pluralize
from gradeflow_core import GradeFlowError, Methods
from gradeflow_core.editing import MethodDraft, load_yaml, method_name, methods_used

from gradeflow_app.accounts.models import User
from gradeflow_app.libraries.collection import BUILTIN_REFS, load_methods, method_files
from gradeflow_app.libraries.method_tests import MethodTests, new_tests, questions_of
from gradeflow_app.libraries.models import (
    NAME_LENGTH,
    NAMESPACE_LENGTH,
    Library,
    Method,
    MethodDraftRecord,
    MethodVersion,
    VersionUse,
)
from gradeflow_app.marking.models import Marking


def name_from(text: str, limit: int) -> str:
    """`text` as lowercase words joined by hyphens, shortened at a hyphen to fit `limit`: `ana-example-edu` from
    `ana@example.edu`. Accents go; other scripts' words go, so it may be empty."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    words = re.findall(r"[a-z0-9]+", ascii_text)
    name = words[0][:limit] if words else ""
    for word in words[1:]:
        if len(name) + 1 + len(word) > limit:
            break
        name = f"{name}-{word}"
    return name


def _names(text: str, limit: int, fallback: str) -> Iterator[str]:
    """`text` as a name that fits `limit`, then numbered from 2, shortened first so the number fits."""
    name = name_from(text, limit) or fallback
    yield name
    for number in count(2):
        suffix = f"-{number}"
        yield name_from(name, limit - len(suffix)) + suffix


def available_namespace(text: str, *, excluding: Library | None = None) -> str:
    """A namespace made from `text`, an email address or a course's name, that no library but `excluding` has."""
    others = Library.objects.exclude(pk=excluding.pk) if excluding else Library.objects.all()
    return next(
        namespace
        for namespace in _names(text, NAMESPACE_LENGTH, "methods")
        if _is_allowed_namespace(namespace) and not others.filter(namespace=namespace).exists()
    )


def _is_allowed_namespace(namespace: str) -> bool:
    """Whether the core lets a library have `namespace`: not its own."""
    try:
        method_name(namespace, "method")
    except GradeFlowError:
        return False
    return True


def available_name(label: str, library: Library, *, excluding: Method | None = None) -> str:
    """A name made from `label` that no method of the library but `excluding` has."""
    others = library.methods.exclude(pk=excluding.pk) if excluding else library.methods.all()
    taken = set(others.values_list("name", flat=True))
    return next(name for name in _names(label, NAME_LENGTH, "method") if name not in taken)


def none_in_use(methods: QuerySet[Method]) -> bool:
    """Whether no saved marking and no method's version uses a version of `methods`. Locks the versions, so a
    marking or version linking one at the same moment has committed and counts, or waits until this commits."""
    versions = MethodVersion.objects.filter(method__in=methods)
    list(versions.select_for_update())
    return not (
        versions.filter(markings__isnull=False).exists()
        or VersionUse.objects.filter(used_version__in=versions).exists()
    )


@transaction.atomic
def rename_namespace(library: Library, text: str) -> None:
    """Make the namespace again from `text`, its owner's new name or email address, unless its methods are used."""
    library = Library.objects.select_for_update().get(pk=library.pk)
    if none_in_use(library.methods.all()):
        library.namespace = available_namespace(text, excluding=library)
        library.save(update_fields=["namespace"])


def taken_label(library: Library, label: str, *, excluding: Method | None = None) -> str | None:
    """What another of the library's methods called `label`, in any case, is called: as published, or as a draft of
    one never published is called."""
    others = library.methods.exclude(pk=excluding.pk) if excluding else library.methods.all()
    names = {method.full_name for method in others}
    versions = MethodVersion.objects.filter(method__library=library).with_used_versions()
    published = [method.label for method in load_methods(method_files(versions)).latest() if method.name in names]
    first_drafts = MethodDraftRecord.objects.filter(method__in=others, method__versions__isnull=True).select_related(
        "method__library"
    )
    drafted = [method_draft(draft.method, draft.definition, draft.tests, Methods()).label for draft in first_drafts]
    return next((taken for taken in [*published, *drafted] if taken.casefold() == label.casefold()), None)


def _require_available_label(library: Library, label: str, *, excluding: Method | None = None) -> None:
    if taken := taken_label(library, label, excluding=excluding):
        raise ValidationError(f"This library already has a method called “{taken}”.")


def method_draft(
    method: Method,
    definition: str,
    tests: MethodTests,
    methods: Methods,
    *,
    version: int | None = None,
    test_values: Mapping[str, Any] = {},
) -> MethodDraft:
    """`definition` as the core reads it as `method`'s `version`, by default its next, with the questions of its
    `tests`."""
    return MethodDraft.from_yaml(
        definition,
        questions_of(tests),
        methods=methods,
        name=method.full_name,
        version=version or method.next_version_number,
        test_values=test_values,
    )


class MakeDraft(Protocol):
    def __call__(self, *, name: str, version: int, label: str, summary: str) -> MethodDraft: ...


def create_method(library: Library, user: User, make_draft: MakeDraft, *, label: str, summary: str) -> Method:
    """A new method called `label` in the library, as `user`'s draft that `make_draft` makes from its name, version,
    label and summary. Raises `ValidationError` if the library has a method called that."""
    _require_available_label(library, label)
    name = available_name(label, library)
    draft = make_draft(name=method_name(library.namespace, name), version=1, label=label, summary=summary)
    method = Method.objects.create(library=library, name=name, created_by=user)
    MethodDraftRecord.objects.create(
        method=method, user=user, definition=draft.to_yaml(), tests=dict(new_tests(draft.questions))
    )
    return method


def _link(version: MethodVersion) -> None:
    """Fix the versions `version` needs. Raises `ValidationError` if one is gone."""
    used = {ref for ref in methods_used(load_yaml(version.definition)) if ref != version.ref}
    used_versions = MethodVersion.objects.linked(used)
    if used - {used_version.ref for used_version in used_versions} - BUILTIN_REFS:
        raise ValidationError("It uses a method that no longer exists.")
    version.used_versions.set(used_versions)


def _methods_with(version: MethodVersion) -> Methods:
    """The core's methods, `version` and what it uses."""
    return load_methods(method_files(MethodVersion.objects.filter(pk=version.pk).with_used_versions()))


def version_name(ref: str) -> str:
    """The name people see for the saved version `ref`, such as “Light” version 2."""
    version = MethodVersion.objects.named([ref]).select_related("method__library").get()
    return f"“{label_of(version)}” version {version.number}"


def label_of(version: MethodVersion) -> str:
    """The name people see for `version`."""
    return method_draft(version.method, version.definition, version.tests, Methods(), version=version.number).label


@transaction.atomic
def publish(draft_record: MethodDraftRecord, user: User, draft: MethodDraft) -> MethodVersion:
    """Publish `draft`, as the core reads `draft_record`, as the method's next version, and end the draft. A new
    name renames the method while nothing uses it. Raises `ValidationError` if the draft can't be published."""
    method = Method.objects.select_for_update().select_related("library").get(pk=draft_record.method_id)
    previous = method.latest_version
    if previous != draft_record.based_on:
        raise ValidationError("A newer version was published since you started. Discard this draft to edit it.")
    if problems := draft.problems:
        raise ValidationError(str(problems[0]))
    label = draft.finalized().label
    _require_available_label(method.library, label, excluding=method)
    if (previous is None or label != label_of(previous)) and none_in_use(Method.objects.filter(pk=method.pk)):
        method.name = available_name(label, method.library, excluding=method)
        draft.name = method.full_name
    version = MethodVersion.objects.create(
        method=method, number=draft.version, definition=draft.to_yaml(), tests=draft_record.tests, created_by=user
    )
    _link(version)
    method.next_version_number = draft.version + 1
    method.save(update_fields=["name", "next_version_number", "updated_at"])
    draft_record.delete()
    return version


@transaction.atomic
def copy_method(version: MethodVersion, library: Library, user: User, label: str) -> Method:
    """`version` as version 1 of a new method called `label` in `library`, with its tests. Raises
    `ValidationError` if the library has a method of that name."""
    _require_available_label(library, label)
    method = Method.objects.create(
        library=library, name=available_name(label, library), next_version_number=2, created_by=user
    )
    draft = method_draft(method, version.definition, version.tests, _methods_with(version), version=1)
    draft.update_metadata(label=label)
    new_version = MethodVersion.objects.create(
        method=method, number=1, definition=draft.to_yaml(), tests=deepcopy(version.tests), created_by=user
    )
    _link(new_version)
    return method


def dependents(method: Method, user: User) -> list[str]:
    """What uses a version of `method`: the markings in `user`'s courses, how many others, and other methods."""
    markings = list(
        Marking.objects.filter(used_versions__method=method)
        .distinct()
        .order_by("pk")
        .select_related("assessment", "question")
    )
    course_ids = set(user.memberships.values_list("course", flat=True))
    my_markings = [marking for marking in markings if marking.assessment.course_id in course_ids]
    uses = [f"{marking.assessment.name} · {marking}" for marking in my_markings]
    if others := len(markings) - len(my_markings):
        uses.append(f"{others} other marking{pluralize(others)}")
    using_versions = MethodVersion.objects.filter(
        pk__in=VersionUse.objects.filter(used_version__method=method).values("version")
    ).exclude(method=method)
    uses += [label_of(version) for version in using_versions.select_related("method__library")]
    return uses


@transaction.atomic
def delete(method: Method, user: User) -> None:
    """Delete a method nothing uses, with its versions. Raises `ValidationError` naming what uses it."""
    method = Method.objects.select_for_update().get(pk=method.pk)
    list(method.versions.select_for_update())  # waits for what is linking them, so it's found below
    if uses := dependents(method, user):
        raise ValidationError(f"Still used by {', '.join(uses)}.")
    method.delete()


@transaction.atomic
def close_library(library: Library) -> None:
    """After its owner goes: delete the library's methods nothing outside them uses, and make the rest private."""
    methods = set(library.methods.all())
    while True:
        still_used = {
            method
            for method in methods
            if method.versions.filter(markings__isnull=False).exists()
            or VersionUse.objects.filter(used_version__method=method).exclude(version__method__in=methods).exists()
        }
        if not still_used:
            break
        methods -= still_used
    Method.objects.filter(pk__in=[method.pk for method in methods]).delete()
    library.methods.update(public=False)
