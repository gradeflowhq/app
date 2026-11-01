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
from gradeflow_core.editing import EditingError, method_ref, new_question
from gradeflow_core.editing import MethodDraft as CoreDraft

from gradeflow_app.accounts.models import User
from gradeflow_app.courses.access import course_member, course_of
from gradeflow_app.courses.models import Course, Member, Role
from gradeflow_app.editor.guide import works_with
from gradeflow_app.libraries import services
from gradeflow_app.libraries.access import Viewer, can_edit, method_viewer
from gradeflow_app.libraries.collection import files, loaded, offers, usable
from gradeflow_app.libraries.forms import CopyForm, NewMethodForm
from gradeflow_app.libraries.models import Library, Method, MethodDraft, MethodVersion
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import go


def library_url(library: Library) -> str:
    return reverse("course_methods", args=[library.course_id]) if library.course_id else reverse("your_methods")


@require_POST
@transaction.atomic
def new(request: Request) -> HttpResponse:
    """A new method, in the library of the course it's made from, `?course=`, or else the person's."""
    course = course_of(request)
    library = course.library if course else request.user.library
    if not can_edit(request.user, library):
        raise PermissionDenied
    form = NewMethodForm(data=request.POST)
    if form.is_valid():
        values = form.cleaned_data
        questions = QuestionSet({"Q1": new_question(values["question_type"])})
        build = partial(CoreDraft.new, questions, methods=usable(request.user, course))
        try:
            method = services.start(library, request.user, build, label=values["label"], summary=values["summary"])
        except ValidationError as error:
            form.add_error("label", error)
        else:
            return go(request, reverse("method_page", args=[method.pk]))
    return render(request, "methods/new.html", {"form": form, "action": request.get_full_path()}, status=422)


@dataclass(frozen=True)
class Listed:
    """A saved method in a list: the version shown, and how many markings in the person's courses use it."""

    method: Method
    version: CoreMethod[Any]
    kinds: list[str]
    used: int
    newer: int | None = None


def _library(library: Library, user: User, *, archived: bool) -> tuple[list[Listed], int]:
    """The library's published methods, archived ones or the others, and how many are archived."""
    mine = Q(versions__markings__assessment__course__members__user=user)
    methods = list(
        library.methods.filter(versions__isnull=False)
        .annotate(used=Count("versions__markings", filter=mine, distinct=True))
        .order_by("name")
    )
    newest = {item.name: item for item in loaded(files(MethodVersion.objects.filter(method__library=library))).latest()}
    shown = [
        Listed(method, newest[method.full_name], works_with(newest[method.full_name]), method.used)
        for method in methods
        if method.archived == archived
    ]
    return shown, sum(method.archived for method in methods)


@dataclass(frozen=True)
class Drafted:
    """A draft of a method with no version yet, as the library lists it: what it says it is so far."""

    draft: MethodDraft
    label: str
    summary: str
    kinds: list[str]


def _drafts(library: Library, user: User) -> list[Drafted]:
    """The person's drafts of the library's methods that have no version yet."""
    drafts = MethodDraft.objects.filter(method__library=library, user=user, method__versions__isnull=True)
    return [_drafted(draft) for draft in drafts.select_related("method__library")]


def _drafted(draft: MethodDraft) -> Drafted:
    method = draft.method
    try:
        core = services.draft_of(method, draft.definition, draft.tests, Methods())
    except EditingError, InvalidError:
        return Drafted(draft, method.name, "", [])
    return Drafted(draft, core.label or method.name, core.summary, works_with(core))


def _context(request: Request, library: Library, **extra: Any) -> dict[str, Any]:
    archived = request.GET.get("archived") == "1"
    methods, archived_count = _library(library, request.user, archived=archived)
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
    context = _context(
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
    action = f"{reverse('method_new')}?course={course.pk}"
    context = _context(
        request,
        library,
        title="Methods",
        member=member,
        can_edit=member.can_edit,
        layout="layouts/course.html",
        action=action,
    )
    context["elsewhere"] = [] if context["archived"] else _elsewhere(course, request.user, library)
    return render(request, "methods/library.html", context)


def _elsewhere(course: Course, user: User, library: Library) -> list[Listed]:
    """The saved methods of other libraries the course's markings use: the newest version used, and any newer
    one the person could update to."""
    owners = list(
        Method.objects.filter(versions__markings__assessment__course=course)
        .exclude(library=library)
        .annotate(used=Count("versions__markings", distinct=True), newest=Max("versions__number"))
        .select_related("library__course", "library__user")
    )
    if not owners:
        return []
    methods = usable(user, course)
    offered = offers(user, course)
    listed = []
    for method in sorted(owners, key=lambda method: method.full_name):
        core = methods.by_ref[method_ref(method.full_name, method.newest)]
        newer = offered.newer(core, methods)
        listed.append(Listed(method, core, works_with(core), method.used, newer.version if newer else None))
    return listed


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def archive(request: Request, viewer: Viewer) -> HttpResponse:
    method = viewer.method
    method.archived = "restore" not in request.POST
    method.save(update_fields=["archived", "updated_at"])
    messages.success(request, "Archived. What uses it keeps working." if method.archived else "Unarchived.")
    return go(request, reverse("published", args=[method.pk]))


@require_POST
@method_viewer(edit=True)
@transaction.atomic
def share(request: Request, viewer: Viewer) -> HttpResponse:
    method = viewer.method
    method.public = request.POST.get("public") == "1"
    method.save(update_fields=["public", "updated_at"])
    messages.success(
        request, "Anyone signed in can use it now." if method.public else "Private. Where it's used, it keeps working."
    )
    return go(request, reverse("published", args=[method.pk]))


@require_POST
@method_viewer(edit=True)
def delete(request: Request, viewer: Viewer) -> HttpResponse:
    method = viewer.method
    try:
        services.delete(method, request.user)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return go(request, reverse("published", args=[method.pk]))
    messages.success(request, "Method deleted.")
    return go(request, library_url(method.library))


@method_viewer()
@transaction.atomic
def copy(request: Request, viewer: Viewer) -> HttpResponse:
    """Copy a version to another library the person can edit, as a new method."""
    version = get_object_or_404(viewer.method.versions, number=request.GET.get("number") or request.POST.get("number"))
    libraries = {str(library.pk): library for library in _editable(request.user) if library != viewer.method.library}
    label = services.label_of(version)
    choices = [(key, _where(library)) for key, library in libraries.items()]
    form = CopyForm(choices, data=request.POST or None, initial={"label": label})
    if request.method == "POST" and form.is_valid():
        library = libraries[form.cleaned_data["library"]]
        try:
            method = services.copy(version, library, request.user, form.cleaned_data["label"])
        except ValidationError as error:
            form.add_error("label", error)
        except GradeFlowError as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, f"Copied to {_where(library)}.")
            return go(request, reverse("published", args=[method.pk]))
    form.fields["label"].widget.attrs["autofocus"] = bool(form["label"].errors)
    context = {
        "form": form,
        "label": label,
        "mine": str(request.user.library.pk),
        "action": reverse("method_copy", args=[viewer.method.pk]),
        "version": version,
    }
    return render(request, "methods/copy.html", context, status=422 if form.is_bound else 200)


def _editable(user: User) -> list[Library]:
    """The person's library, then those of the courses they edit, newest first."""
    courses = Library.objects.filter(course__members__user=user, course__members__role__in=Role.EDITOR.at_least())
    return [user.library, *courses.select_related("course").order_by("-course__created_at")]


def _where(library: Library) -> str:
    return "Your methods" if library.user_id else str(library.course)
