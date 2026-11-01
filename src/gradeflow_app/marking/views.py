from collections.abc import Mapping
from functools import partial
from typing import Any, Literal, cast
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import BadRequest, PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseLocation
from gradeflow_core import Check, GradeFlowError
from gradeflow_core.editing import DECISIONS, Decision, MarkingDraft, answer_decisions, dump_yaml, mark_by_example
from gradeflow_core.editing import MethodDraft as CoreDraft

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.models import Assessment, Membership
from gradeflow_app.courses.models import Role
from gradeflow_app.editor import operations
from gradeflow_app.editor.guide import sections_of
from gradeflow_app.editor.picker import PickerUse, check_picker, picker
from gradeflow_app.editor.presenters import Editor, editor
from gradeflow_app.jobs.queue import stop
from gradeflow_app.libraries import services as libraries
from gradeflow_app.libraries.collection import offers, usable
from gradeflow_app.libraries.forms import SaveMethodForm
from gradeflow_app.live.topics import publish, topic
from gradeflow_app.marking import presenters, previews
from gradeflow_app.marking.answers.column import column, editing, preview_bar
from gradeflow_app.marking.checks import check_for, check_of, checks, marked_keys, methods_for, questions_marked
from gradeflow_app.marking.models import Draft, Marking, Subject
from gradeflow_app.marking.presenters import drafted_method, saved_summary
from gradeflow_app.marking.remarks import delete_marking, schedule_marking
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.questions.models import Question
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import go, morph


def _question_group(membership: Membership, marking_id: UUID) -> Marking:
    return get_object_or_404(Marking, pk=marking_id, assessment=membership.assessment, question=None)


def _drafts_changed(request: Request, assessment: Assessment) -> None:
    publish(topic("drafts", assessment.pk, request.user.pk))


@member()
def questions(request: Request, membership: Membership) -> HttpResponse:
    assessment = membership.assessment
    questions = list(assessment.questions.all())
    if not questions:
        return render(request, "marking/no_questions.html", {"membership": membership})
    marked = marked_keys(checks(assessment))
    first = next((question for question in questions if question.key not in marked), questions[0])
    return redirect("question", assessment.pk, first.pk)


@member()
def question(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    question = membership.question(question_id)
    subject = Subject.of(membership.assessment, question)
    workspace = editing(request, membership, subject)
    accepted = checks(membership.assessment)
    context = _marking(request, membership, question, subject, workspace, accepted) | {
        "question": question,
        "questions": presenters.question_list(membership, str(question.pk), accepted),
        "answers": column(request, membership, subject, workspace),
    }
    return render(request, "marking/workspace.html", context)


@member()
def question_list(request: Request, membership: Membership) -> HttpResponse:
    """The question list alone, to refresh it in place, highlighting what the page asking for it shows."""
    questions = presenters.question_list(membership, request.GET.get("shown", ""))
    return render(request, "marking/question_list.html#list", {"membership": membership, "questions": questions})


def _marking(
    request: Request,
    membership: Membership,
    question: Question,
    subject: Subject,
    workspace: Workspace | None,
    accepted: Mapping[Marking, Check] | None = None,
) -> dict[str, Any]:
    """The marking column of a question: the user's draft in `workspace`, or else the saved marking."""
    if workspace is not None:
        return {"membership": membership, "editor": _editor(request, workspace, request.GET.get("path", ""))}
    return {
        "membership": membership,
        "question": question,
        "panel": presenters.panel(membership, question, subject.marking, accepted),
    }


def _editor(request: Request, workspace: Workspace, path: str) -> Editor:
    return editor(workspace, path, saving=request.POST.get("op") == "save", more=bool(request.POST.get("more")))


def _column(request: Request, membership: Membership, question: Question, *, answers: bool = False) -> HttpResponse:
    """The marking column; with `answers`, the answers column too, swapped in beside it."""
    subject = Subject.of(membership.assessment, question)
    workspace = editing(request, membership, subject)
    context = _marking(request, membership, question, subject, workspace)
    if answers:
        context["answers_html"] = column(request, membership, subject, workspace, oob=True)
    return render(request, "marking/panel.html", context)


@member()
def marking_panel(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    return _column(request, membership, membership.question(question_id))


@require_POST
@member(Role.EDITOR)
@transaction.atomic
def start_marking(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    """A draft of new marking with the chosen method, or of the saved marking."""
    membership.assessment.lock()
    question = membership.question(question_id)
    subject = Subject.of(membership.assessment, question)
    if method := request.POST.get("method"):
        definition = _new_marking(request, subject, method)
    elif subject.marking is not None:
        definition = subject.marking.definition
    else:
        raise Http404
    _start_draft(request, subject, definition)
    return _column(request, membership, question, answers=True)


@transaction.atomic
def _start_draft(request: Request, subject: Subject, definition: str) -> None:
    """Start the user's draft of the subject's marking from `definition`, replacing any they had."""
    subject.assessment.lock()
    owner = {"question": subject.question} if subject.question else {"marking": subject.marking}
    Draft.objects.update_or_create(
        user=request.user,
        **owner,
        defaults={
            "assessment": subject.assessment,
            "definition": definition,
            "based_on": subject.marking.updated_at if subject.marking else None,
            "preview": None,
            "previewed": None,
        },
    )
    _drafts_changed(request, subject.assessment)


def _new_marking(request: Request, subject: Subject, method: str) -> str:
    try:
        draft = MarkingDraft.new(
            method,
            subject.assessment.question_set(),
            subject.key,
            methods=usable(request.user, subject.assessment.course),
        )
    except GradeFlowError:
        raise Http404 from None
    return dump_yaml(draft.data)


@member(Role.EDITOR)
@transaction.atomic
def draft_view(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    """The editor at `path`; posting saves fields to the draft and applies the operation in `op`, if any."""
    try:
        workspace = Workspace.of(request, membership, draft, lock=request.method == "POST")
    except Http404:
        # Saving or discarding can remove a draft before its queued live refresh arrives.
        if request.method == "GET" and request.GET.get("status"):
            return HttpResponse(status=204)
        raise
    path = request.POST.get("path", request.GET.get("path", ""))
    if request.method != "POST":
        if request.GET.get("status"):
            return render(
                request,
                "marking/status.html",
                {
                    "editor": _editor(request, workspace, path),
                    "answers_html": column(request, membership, workspace.subject, workspace, oob=True),
                },
            )
        return render(
            request, "marking/panel.html", {"membership": membership, "editor": _editor(request, workspace, path)}
        )
    typing = not request.POST.get("op")
    asked = workspace.asks(path) if workspace.checks.is_check(path) else None
    decisions = answer_decisions(workspace.checks.data, methods=workspace.methods)
    method = drafted_method(workspace)
    shown = _editor(request, workspace, _apply(request, workspace, path))
    _list_method(request, workspace, method)
    context: dict[str, Any] = {"membership": membership, "editor": shown}
    # Typing keeps the form in place unless its fields change; answer decisions refresh beside it, and so does the
    # preview once the draft is back to what it previewed.
    if typing and workspace.asks(shown.path) == asked:
        answers_html = (
            column(request, membership, workspace.subject, workspace, oob=True)
            if workspace.subject.question is None
            or decisions != answer_decisions(workspace.checks.data, methods=workspace.methods)
            or not previews.stale(workspace)
            else preview_bar(request, workspace)
        )
        saved = '<span id="editor-status" hx-swap-oob="true" class="text-sm text-muted">Draft saved</span>'
        context |= {"live": True, "live_html": mark_safe(answers_html + saved)}
        return render(request, "marking/check.html#live", context)
    context["answers_html"] = column(request, membership, workspace.subject, workspace, oob=True)
    response = render(request, "marking/panel.html", context)
    return morph(response, "#marking-panel") if typing else response


def _list_method(request: Request, workspace: Workspace, before: str) -> None:
    """Refresh the user's question list where it names the draft's method, once that changed from `before`."""
    if drafted_method(workspace) != before:
        _drafts_changed(request, workspace.subject.assessment)


def _apply(request: Request, workspace: Workspace, path: str) -> str:
    try:
        path = operations.apply(workspace, request.POST, path)
    except GradeFlowError:
        raise Http404 from None
    workspace.save()
    return path


@require_POST
@member(Role.EDITOR)
def preview(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    workspace = Workspace.of(request, membership, draft)
    previews.run(request, membership, workspace)
    return HttpResponse(column(request, membership, workspace.subject, workspace))


@member()
def question_group_answers(request: Request, membership: Membership, marking_id: UUID) -> HttpResponse:
    marking = Marking.objects.filter(pk=marking_id, assessment=membership.assessment, question=None).first()
    if marking is None:
        # Removed while its page was open: the page goes to the questions.
        if not request.htmx:
            raise Http404
        return HttpResponseLocation(reverse("questions", args=[membership.assessment.pk]))
    subject = Subject.of(membership.assessment, marking=marking)
    return HttpResponse(column(request, membership, subject, editing(request, membership, subject)))


@member(Role.EDITOR)
def question_group_draft_answers(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    try:
        workspace = Workspace.of(request, membership, draft, question=None)
    except Http404:
        return HttpResponse(status=204)  # saved or discarded since the refresh was queued
    return HttpResponse(column(request, membership, workspace.subject, workspace))


@require_POST
@member(Role.EDITOR)
@transaction.atomic
def save(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    workspace = Workspace.of(request, membership, draft, lock=True)
    path = _apply(request, workspace, request.POST.get("path", ""))
    if not workspace.valid or workspace.conflicts:
        path = path if workspace.problems_at(path, saving=True) else next((p.check for p in workspace.problems), path)
        context = {"membership": membership, "editor": _editor(request, workspace, path)}
        return render(request, "marking/editor.html", context)
    subject = workspace.subject
    marking = subject.marking or Marking(assessment=membership.assessment, question=subject.question)
    marking.definition = workspace.draft.definition
    marking.save()
    stop(workspace.draft.preview)
    workspace.draft.delete()
    schedule_marking(marking, request.user, endpoint(request))
    publish(topic("questions", membership.assessment.pk))
    messages.success(request, "Saved. Marking students…")
    if subject.question is None:
        return HttpResponseLocation(reverse("question_group", args=[membership.assessment.pk, marking.pk]))
    return _column(request, membership, subject.question, answers=True)


@require_POST
@member(Role.EDITOR)
@transaction.atomic
def discard(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    workspace = Workspace.of(request, membership, draft, lock=True)
    subject = workspace.subject
    stop(workspace.draft.preview)
    workspace.draft.delete()
    _drafts_changed(request, membership.assessment)
    messages.success(request, "Changes discarded.")
    if subject.question is not None:
        return _column(request, membership, subject.question, answers=True)
    assessment = membership.assessment
    if subject.marking is not None:
        return HttpResponseLocation(reverse("question_group", args=[assessment.pk, subject.marking.pk]))
    return HttpResponseLocation(reverse("question_group_new", args=[assessment.pk]))


@require_POST
@member(Role.EDITOR)
@transaction.atomic
def remove(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    question = membership.question(question_id)
    membership.assessment.lock()
    if marking := Marking.objects.filter(question=question).first():
        delete_marking(marking)
    publish(topic("questions", membership.assessment.pk))
    messages.success(request, "Marking removed.")
    return _column(request, membership, question)


@member()
def history(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    question = membership.question(question_id)
    methods, questions = usable(request.user, membership.course), membership.assessment.question_set()
    versions = Marking.history.filter(question_id=question.pk).select_related("history_user").order_by("-history_date")
    current = Marking.objects.filter(question=question).exists()
    entries = [
        {
            "version": version,
            "summary": saved_summary(version.definition, question.key, methods, questions),
            "current": current and index == 0,
        }
        for index, version in enumerate(version for version in versions if version.history_type != "-")
    ]
    return render(request, "marking/history.html", {"membership": membership, "question": question, "entries": entries})


@require_POST
@member(Role.EDITOR)
def restore(request: Request, membership: Membership, question_id: UUID, version: int) -> HttpResponse:
    question = membership.question(question_id)
    historical = get_object_or_404(Marking.history, history_id=version, question_id=question.pk)
    _start_draft(request, Subject.of(membership.assessment, question), historical.definition)
    messages.success(request, "Restored as your draft. Save it to use it.")
    return HttpResponseLocation(reverse("question", args=[membership.assessment.pk, question.pk]))


@require_POST
@member(Role.EDITOR)
@transaction.atomic
def mark_example(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    """Changes the draft being edited so an answer group's answers get the marks given them."""
    membership.assessment.lock()
    question = membership.question(question_id)
    subject = Subject.of(membership.assessment, question)
    draft = subject.draft_of(request.user, lock=True)
    if draft is None:
        raise Http404
    workspace = Workspace(draft)
    answers, given = request.POST.getlist("answer"), _given(request)
    try:
        marked = mark_by_example(
            workspace.checks.data, answers, given, methods=workspace.methods, question=question.core
        )
    except GradeFlowError as error:
        messages.error(request, str(error))
    else:
        method = drafted_method(workspace)
        workspace.checks.replace(marked)
        workspace.save()
        _list_method(request, workspace, method)
    context = {
        "membership": membership,
        "editor": _editor(request, workspace, ""),
        "answers_html": column(request, membership, subject, workspace, oob=True),
    }
    return render(request, "marking/panel.html", context)


@member()
@transaction.atomic
def question_group_new(request: Request, membership: Membership) -> HttpResponse:
    if request.method == "POST":
        if not membership.can_edit:
            raise PermissionDenied
        membership.assessment.lock()
        definition = _new_marking(request, Subject.of(membership.assessment), request.POST.get("method", ""))
        draft = Draft.objects.create(assessment=membership.assessment, user=request.user, definition=definition)
        return redirect("question_group_draft", membership.assessment.pk, draft.pk)
    context = {
        "membership": membership,
        "questions": presenters.question_list(membership, "new"),
        "methods": [
            method
            for section in sections_of(
                methods_for(request.user, membership.assessment), offers(request.user, membership.course)
            )
            if section.tab != "public"
            for method, _ in section.entries
        ],
    }
    return render(request, "marking/question_group_new.html", context)


def _given(request: Request) -> Decision | Literal["cancel"]:
    given = request.POST.get("given", "")
    if given in DECISIONS or given == "cancel":
        return cast("Decision | Literal['cancel']", given)
    raise BadRequest


@member(Role.EDITOR)
def question_methods(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    question = membership.question(question_id)
    methods = methods_for(request.user, membership.assessment, question.key)
    action = reverse("marking_start", args=[membership.assessment.pk, question.pk])
    use = PickerUse(action=action, field="method", target="#marking-panel", swap="outerHTML")
    sections = sections_of(methods, offers(request.user, membership.course))
    return picker(request, sections, request.GET.get("selected", ""), use, membership.course, question.core.type)


@member(Role.EDITOR)
def draft_methods(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    """The method picker for the check at `target` of the draft, which opens the editor at `path` after."""
    workspace = Workspace.of(request, membership, draft)
    args = [membership.assessment.pk, workspace.draft.pk]
    use = PickerUse(
        action=reverse("draft", args=args),
        field="value",
        target="#marking-panel",
        swap="outerHTML",
        fields={"path": request.GET.get("path", request.GET.get("target", ""))},
    )
    here = reverse("draft_methods", args=args)
    return check_picker(request, workspace.checks, workspace.offers, membership.course, use, here)


@member(Role.EDITOR)
def question_group_methods(request: Request, membership: Membership) -> HttpResponse:
    use = PickerUse(action=reverse("question_group_new", args=[membership.assessment.pk]), field="method")
    sections = sections_of(methods_for(request.user, membership.assessment), offers(request.user, membership.course))
    return picker(request, sections, request.GET.get("selected", ""), use, membership.course)


@member()
def question_group(request: Request, membership: Membership, marking_id: UUID) -> HttpResponse:
    marking = _question_group(membership, marking_id)
    subject = Subject.of(membership.assessment, marking=marking)
    workspace = editing(request, membership, subject)
    accepted = checks(membership.assessment)
    saved = presenters.saved_group(membership.assessment, marking, accepted.get(marking))
    context: dict[str, Any] = {
        "membership": membership,
        "questions": presenters.question_list(membership, str(marking.pk), accepted),
        "question_group": marking,
        "question_group_marks": saved.marks,
        "question_group_keys": saved.keys,
        "summary": saved.summary,
        "answers": column(request, membership, subject, workspace),
    }
    if workspace is not None:
        context["editor"] = _editor(request, workspace, request.GET.get("path", ""))
    return render(request, "marking/question_group.html", context)


@require_POST
@member(Role.EDITOR)
def question_group_edit(request: Request, membership: Membership, marking_id: UUID) -> HttpResponse:
    marking = _question_group(membership, marking_id)
    _start_draft(request, Subject.of(membership.assessment, marking=marking), marking.definition)
    return redirect("question_group", membership.assessment.pk, marking.pk)


@member(Role.EDITOR)
def question_group_draft(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    workspace = Workspace.of(request, membership, draft, question=None)
    if workspace.draft.marking is not None:
        return redirect("question_group", membership.assessment.pk, workspace.draft.marking.pk)
    context = {
        "membership": membership,
        "questions": presenters.question_list(membership, str(workspace.draft.pk)),
        "editor": _editor(request, workspace, request.GET.get("path", "")),
        "answers": column(request, membership, workspace.subject, workspace),
    }
    return render(request, "marking/question_group.html", context)


@require_POST
@member(Role.EDITOR)
@transaction.atomic
def question_group_remove(request: Request, membership: Membership, marking_id: UUID) -> HttpResponse:
    membership.assessment.lock()
    marking = _question_group(membership, marking_id)
    marked = sorted(questions_marked(marking, check_for(marking)))
    delete_marking(marking)
    publish(topic("questions", membership.assessment.pk))
    messages.success(request, f"Removed the question group marking {', '.join(marked) or 'no questions'}.")
    return redirect("questions", membership.assessment.pk)


@member(Role.EDITOR)
def save_as_method(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    marking = get_object_or_404(Marking, question=question_id, question__assessment=membership.assessment)
    return _save_as_method(
        request, membership, marking, reverse("method_save", args=[membership.assessment.pk, question_id])
    )


@member(Role.EDITOR)
def question_group_save_as_method(request: Request, membership: Membership, marking_id: UUID) -> HttpResponse:
    marking = _question_group(membership, marking_id)
    action = reverse("question_group_method_save", args=[membership.assessment.pk, marking_id])
    return _save_as_method(request, membership, marking, action)


@transaction.atomic
def _save_as_method(request: Request, membership: Membership, marking: Marking, action: str) -> HttpResponse:
    """A draft of a new method, in the course's library, that marks as the marking does."""
    course = membership.course
    form = SaveMethodForm(data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        methods = usable(request.user, course)
        check = check_of(marking.definition, methods)
        if check is None:
            form.add_error(None, "Fix the marking before saving it as a method.")
        else:
            build = partial(CoreDraft.from_marking, check, membership.assessment.question_set(), methods=methods)
            try:
                method = libraries.start(course.library, request.user, build, **form.cleaned_data)
            except ValidationError as error:
                form.add_error("label", error)
            else:
                return go(request, reverse("method_page", args=[method.pk]))
    context = {"form": form, "action": action}
    return render(request, "methods/save.html", context, status=422 if form.is_bound else 200)
