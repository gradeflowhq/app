"""Libraries of saved methods: a person's and a course's, saving a new method to one, and archiving, sharing, copying
or deleting a method."""

from dataclasses import dataclass
from functools import partial
from typing import Any

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from gradeflow_core import GradeFlowError, InvalidError, Methods, QuestionSet
from gradeflow_core import Method as CoreMethod
from gradeflow_core.editing import EditingError, MethodDraft, method_ref, new_question

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.access import course_from_query, course_member
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.editor.guide import works_with
from gradeflow_app.libraries import services
from gradeflow_app.libraries.access import MethodAccess, can_edit, method_viewer
from gradeflow_app.libraries.collection import load_methods, method_files, offers, usable
from gradeflow_app.libraries.forms import CopyForm, NewMethodForm
from gradeflow_app.libraries.models import Library, Method, MethodDraftRecord, MethodVersion
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import redirect_page


def library_url(library: Library) -> str:
    return reverse("course_methods", args=[library.course_id]) if library.course_id else reverse("your_methods")


@require_POST
@transaction.atomic
def new_method(request: Request) -> HttpResponse:
    """A new method, in the library of the course it's made from, `?course=`, or else the person's."""
    course = course_from_query(request)
    library = course.library if course else request.user.library
    if not can_edit(request.user, library):
        raise PermissionDenied
    form = NewMethodForm(data=request.POST)
    if form.is_valid():
        cleaned = form.cleaned_data
        questions = QuestionSet({"Q1": new_question(cleaned["question_type"])})
        make_draft = partial(MethodDraft.new, questions, methods=usable(request.user, course))
        try:
            method = services.create_method(
                library, request.user, make_draft, label=cleaned["label"], summary=cleaned["summary"]
            )
        except ValidationError as error:
            form.add_error("label", error)
        else:
            return redirect_page(request, reverse("method_page", args=[method.pk]))
    return render(request, "methods/new.html", {"form": form, "action": request.get_full_path()}, status=422)


@dataclass(frozen=True)
class LibraryMethod:
    """A saved method in a list: the version shown, and how many markings in the person's courses use it."""

    method: Method
    version: CoreMethod[Any]
    type_labels: list[str]
    marking_count: int
    newer_version_number: int | None = None


def _published_methods(library: Library, user: User, *, archived: bool) -> tuple[list[LibraryMethod], int]:
    """The library's published methods, archived ones or the others, and how many are archived."""
    in_my_courses = Q(versions__markings__assessment__course__members__user=user)
    methods = list(
        library.methods.filter(versions__isnull=False)
        .annotate(marking_count=Count("versions__markings", filter=in_my_courses, distinct=True))
        .order_by("name")
    )
    versions = MethodVersion.objects.filter(method__library=library)
    newest = {method.name: method for method in load_methods(method_files(versions)).latest()}
    shown = [
        LibraryMethod(method, newest[method.full_name], works_with(newest[method.full_name]), method.marking_count)
        for method in methods
        if method.archived == archived
    ]
    return shown, sum(method.archived for method in methods)


@dataclass(frozen=True)
class UnpublishedDraft:
    """A draft of a method with no version yet, as the library lists it: what it says it is so far."""

    draft: MethodDraftRecord
    label: str
    summary: str
    type_labels: list[str]


def _drafts(library: Library, user: User) -> list[UnpublishedDraft]:
    """The person's drafts of the library's methods that have no version yet."""
    drafts = MethodDraftRecord.objects.filter(method__library=library, user=user, method__versions__isnull=True)
    return [_unpublished_draft(record) for record in drafts.select_related("method__library")]


def _unpublished_draft(record: MethodDraftRecord) -> UnpublishedDraft:
    method = record.method
    try:
        draft = services.method_draft(method, record.definition, record.tests, Methods())
    except EditingError, InvalidError:
        return UnpublishedDraft(record, method.name, "", [])
    return UnpublishedDraft(record, draft.label or method.name, draft.summary, works_with(draft))


def _library_context(request: Request, library: Library, **extra: Any) -> dict[str, Any]:
    archived = request.GET.get("archived") == "1"
    methods, archived_count = _published_methods(library, request.user, archived=archived)
    return {
        "library": library,
        "methods": methods,
        "drafts": [] if archived else _drafts(library, request.user),
        "archived": archived,
        "archived_count": archived_count,
        "form": NewMethodForm(),
        **extra,
    }


def your_methods(request: Request) -> HttpResponse:
    context = _library_context(
        request,
        request.user.library,
        title="Your methods",
        can_edit=True,
        layout="layouts/methods.html",
        action=reverse("method_new"),
    )
    return render(request, "methods/library.html", context)


@course_member()
def course_methods(request: Request, member: Member) -> HttpResponse:
    course = member.course
    library = course.library
    action = reverse("method_new", query={"course": course.pk})
    context = _library_context(
        request,
        library,
        title="Methods",
        member=member,
        can_edit=member.can_edit,
        layout="layouts/course.html",
        action=action,
    )
    context["other_library_methods"] = (
        [] if context["archived"] else _other_library_methods(course, request.user, library)
    )
    return render(request, "methods/library.html", context)


def _other_library_methods(course: Course, user: User, library: Library) -> list[LibraryMethod]:
    """The saved methods of other libraries the course's markings use: the newest version used, and any newer
    one the person could update to."""
    used_methods = list(
        Method.objects.filter(versions__markings__assessment__course=course)
        .exclude(library=library)
        .annotate(marking_count=Count("versions__markings", distinct=True), newest=Max("versions__number"))
        .select_related("library__course", "library__user")
    )
    if not used_methods:
        return []
    methods = usable(user, course)
    offered = offers(user, course)
    rows = []
    for method in sorted(used_methods, key=lambda method: method.full_name):
        version = methods.by_ref[method_ref(method.full_name, method.newest)]
        newer_version = offered.newer_version(version, methods)
        rows.append(
            LibraryMethod(
                method,
                version,
                works_with(version),
                method.marking_count,
                newer_version.version if newer_version else None,
            )
        )
    return rows


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def archive(request: Request, access: MethodAccess) -> HttpResponse:
    method = access.method
    method.archived = "unarchive" not in request.POST
    method.save(update_fields=["archived", "updated_at"])
    messages.success(request, "Archived. What uses it keeps working." if method.archived else "Unarchived.")
    return redirect_page(request, reverse("method_latest", args=[method.pk]))


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def set_public(request: Request, access: MethodAccess) -> HttpResponse:
    method = access.method
    method.public = request.POST.get("public") == "1"
    method.save(update_fields=["public", "updated_at"])
    messages.success(
        request, "Anyone signed in can use it now." if method.public else "Private. Where it's used, it keeps working."
    )
    return redirect_page(request, reverse("method_latest", args=[method.pk]))


@require_POST
@method_viewer(edit=True)
def delete(request: Request, access: MethodAccess) -> HttpResponse:
    method = access.method
    try:
        services.delete(method, request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect_page(request, reverse("method_latest", args=[method.pk]))
    messages.success(request, "Method deleted.")
    return redirect_page(request, library_url(method.library))


@method_viewer()
@transaction.atomic
def copy_method(request: Request, access: MethodAccess) -> HttpResponse:
    """Copy a version to another library the person can edit, as a new method."""
    version = get_object_or_404(access.method.versions, number=request.GET.get("number") or request.POST.get("number"))
    libraries = {
        str(library.pk): library for library in _editable_libraries(request.user) if library != access.method.library
    }
    label = services.label_of(version)
    choices = [(key, _library_name(library)) for key, library in libraries.items()]
    form = CopyForm(choices, data=request.POST or None, initial={"label": label})
    if request.method == "POST" and form.is_valid():
        library = libraries[form.cleaned_data["library"]]
        try:
            method = services.copy_method(version, library, request.user, form.cleaned_data["label"])
        except ValidationError as error:
            form.add_error("label", error)
        except GradeFlowError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"Copied to {_library_name(library)}.")
            return redirect_page(request, reverse("method_latest", args=[method.pk]))
    form.fields["label"].widget.attrs["autofocus"] = bool(form["label"].errors)
    context = {
        "form": form,
        "label": label,
        "own_library": str(request.user.library.pk),
        "action": reverse("method_copy", args=[access.method.pk]),
        "version": version,
    }
    return render(request, "methods/copy.html", context, status=422 if form.is_bound else 200)


def _editable_libraries(user: User) -> list[Library]:
    """The person's library, then those of the courses they edit, newest first."""
    course_libraries = Library.objects.filter(
        course__members__user=user, course__members__role__in=Role.EDITOR.and_above()
    )
    return [user.library, *course_libraries.select_related("course").order_by("-course__created_at")]


def _library_name(library: Library) -> str:
    return "Your methods" if library.user_id else str(library.course)
