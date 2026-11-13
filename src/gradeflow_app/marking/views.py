from collections.abc import Mapping
from functools import partial
from typing import Any, Literal, cast
from uuid import UUID

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django_htmx.http import HttpResponseLocation, push_url
from gradeflow_core import GradeFlowError, Step
from gradeflow_core.editing import Decision, MarkingDraft, MethodDraft, dump_yaml

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.courses.models import Role
from gradeflow_app.editor import operations
from gradeflow_app.editor.guide import choices, sections_of
from gradeflow_app.editor.picker import PickerForm, picker, step_picker
from gradeflow_app.editor.presenters import Editor, editor
from gradeflow_app.libraries import services as libraries
from gradeflow_app.libraries.collection import loaded_usable_methods, offers, usable
from gradeflow_app.libraries.forms import SaveMethodForm
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.marking import presenters, previews
from gradeflow_app.marking.answers.column import answers_column_html, draft_workspace, preview_bar
from gradeflow_app.marking.checks import assessment_checks, check_for, check_of, methods_for, questions_marked
from gradeflow_app.marking.models import Marking, MarkingDraftRecord, MarkingSubject
from gradeflow_app.marking.presenters import drafted_method, saved_summary
from gradeflow_app.marking.remarks import delete_marking
from gradeflow_app.marking.workspace import MarkingWorkspace, notify_drafts_changed, start_draft
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.questions.models import Question
from gradeflow_app.requests import Request
from gradeflow_app.ui.fragments import morphed_into, redirect_page


def _question_group(access: AssessmentAccess, marking_id: UUID) -> Marking:
    return get_object_or_404(Marking, pk=marking_id, assessment=access.assessment, question=None)


@assessment_member()
def questions(request: Request, access: AssessmentAccess) -> HttpResponse:
    """The first question; a click in the app gets its page at once, saving a round trip."""
    assessment = access.assessment
    first = assessment.questions.first()
    if first is None:
        return render(request, "marking/no_questions.html", {"access": access})
    if not request.htmx:
        return redirect("question", assessment.pk, first.pk)
    page = _question(request, access, first, assessment_checks(assessment))
    return push_url(page, reverse("question", args=[assessment.pk, first.pk]))


@assessment_member()
def question(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    return _question(request, access, access.question(question_id), assessment_checks(access.assessment))


def _question(
    request: Request, access: AssessmentAccess, question: Question, accepted: Mapping[Marking, Step]
) -> HttpResponse:
    subject = MarkingSubject.of(access.assessment, question)
    workspace = draft_workspace(request, access, subject)
    context = _marking_column_context(request, access, question, subject, workspace, accepted) | {
        "question": question,
        "question_list": presenters.question_list(access, str(question.pk), accepted),
        "answers": answers_column_html(request, access, subject, workspace, accepted=accepted),
    }
    return render(request, "marking/workspace.html", context)


@assessment_member()
def question_list(request: Request, access: AssessmentAccess) -> HttpResponse:
    """The question list alone, to refresh it in place, highlighting what the page asking for it shows."""
    question_list = presenters.question_list(access, request.GET.get("question", ""))
    context = {"access": access, "question_list": question_list}
    return render(request, "marking/question_list.html#list", context)


def _marking_column_context(
    request: Request,
    access: AssessmentAccess,
    question: Question,
    subject: MarkingSubject,
    workspace: MarkingWorkspace | None,
    accepted: Mapping[Marking, Step] | None = None,
) -> dict[str, Any]:
    """The marking column of a question: the user's draft in `workspace`, or else the saved marking."""
    if workspace is not None:
        return {"access": access, "editor": _editor(request, workspace, request.GET.get("path", ""))}
    return {
        "access": access,
        "question": question,
        "panel": presenters.panel(access, question, subject.marking, accepted),
    }


def _editor(request: Request, workspace: MarkingWorkspace, path: str) -> Editor:
    return editor(
        workspace, path, saving=request.POST.get("operation") == "save", more_open=bool(request.POST.get("more_open"))
    )


def _render_marking_column(
    request: Request, access: AssessmentAccess, question: Question, *, answers: bool = False
) -> HttpResponse:
    """The marking column; with `answers`, the answers column too, swapped in beside it."""
    subject = MarkingSubject.of(access.assessment, question)
    workspace = draft_workspace(request, access, subject)
    context = _marking_column_context(request, access, question, subject, workspace)
    if answers:
        context["answers_html"] = answers_column_html(request, access, subject, workspace, out_of_band=True)
    return render(request, "marking/panel.html", context)


@assessment_member()
def marking_panel(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    return _render_marking_column(request, access, access.question(question_id))


@require_POST
@assessment_member(Role.EDITOR)
@transaction.atomic
def start_marking(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    """A draft of new marking with the chosen method, or of the saved marking."""
    access.assessment.lock()
    question = access.question(question_id)
    subject = MarkingSubject.of(access.assessment, question)
    if method := request.POST.get("method"):
        definition = _new_marking(request, subject, method)
    elif subject.marking is not None:
        definition = subject.marking.definition
    else:
        raise Http404
    start_draft(subject, request.user, definition)
    return _render_marking_column(request, access, question, answers=True)


def _new_marking(request: Request, subject: MarkingSubject, method: str) -> str:
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


@assessment_member(Role.EDITOR)
@transaction.atomic
def draft_view(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    """The editor at `path`; posting saves fields to the draft and applies the operation in `operation`, if any."""
    try:
        workspace = MarkingWorkspace.of(request, access, draft_id, lock=request.method == "POST")
    except Http404:
        # Saving or discarding can remove a draft before its queued live refresh arrives.
        if request.method == "GET" and request.GET.get("status_only"):
            return HttpResponse(status=204)
        raise
    path = request.POST.get("path", request.GET.get("path", ""))
    if request.method != "POST":
        if request.GET.get("status_only"):
            return render(
                request,
                "marking/status.html",
                {
                    "editor": _editor(request, workspace, path),
                    "answers_html": answers_column_html(
                        request, access, workspace.subject, workspace, out_of_band=True
                    ),
                },
            )
        return render(request, "marking/panel.html", {"access": access, "editor": _editor(request, workspace, path)})
    fields_only = not request.POST.get("operation")
    structure_before = workspace.editor_structure(path) if workspace.draft.is_step(path) else None
    decisions = workspace.draft.answer_decisions()
    method = drafted_method(workspace.draft)
    next_editor = _editor(request, workspace, _apply(request, workspace, path))
    _refresh_list_if_method_changed(request, workspace, method)
    context: dict[str, Any] = {"access": access, "editor": next_editor}
    # Typing keeps the form in place unless its fields change; answer decisions refresh beside it, and so does the
    # preview once the draft is back to what it previewed.
    if fields_only and workspace.editor_structure(next_editor.path) == structure_before:
        answers_html = (
            answers_column_html(request, access, workspace.subject, workspace, out_of_band=True)
            if workspace.subject.question is None
            or decisions != workspace.draft.answer_decisions()
            or not previews.preview_is_stale(workspace)
            else preview_bar(request, workspace)
        )
        context |= {"fields_only_update": True, "update_html": answers_html}
        return render(request, "marking/step.html#notices", context)
    context["answers_html"] = answers_column_html(request, access, workspace.subject, workspace, out_of_band=True)
    response = render(request, "marking/panel.html", context)
    return morphed_into(response, "#marking-panel") if fields_only else response


def _refresh_list_if_method_changed(request: Request, workspace: MarkingWorkspace, before: str) -> None:
    """Refresh the user's question list where it names the draft's method, once that changed from `before`."""
    if drafted_method(workspace.draft) != before:
        notify_drafts_changed(workspace.subject.assessment, request.user)


def _apply(request: Request, workspace: MarkingWorkspace, path: str) -> str:
    try:
        path = operations.apply(workspace, request.POST, path)
    except GradeFlowError:
        raise Http404 from None
    workspace.save()
    return path


@require_POST
@assessment_member(Role.EDITOR)
def preview(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    workspace = MarkingWorkspace.of(request, access, draft_id)
    previews.run_preview(request, access, workspace)
    return HttpResponse(answers_column_html(request, access, workspace.subject, workspace))


@assessment_member()
def question_group_answers(request: Request, access: AssessmentAccess, marking_id: UUID) -> HttpResponse:
    marking = Marking.objects.filter(pk=marking_id, assessment=access.assessment, question=None).first()
    if marking is None:
        # Removed while its page was open: the page goes to the questions.
        if not request.htmx:
            raise Http404
        return HttpResponseLocation(reverse("questions", args=[access.assessment.pk]))
    subject = MarkingSubject.of(access.assessment, marking=marking)
    return HttpResponse(answers_column_html(request, access, subject, draft_workspace(request, access, subject)))


@assessment_member(Role.EDITOR)
def question_group_draft_answers(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    try:
        workspace = MarkingWorkspace.of(request, access, draft_id, question=None)
    except Http404:
        return HttpResponse(status=204)  # saved or discarded since the refresh was queued
    return HttpResponse(answers_column_html(request, access, workspace.subject, workspace))


@require_POST
@assessment_member(Role.EDITOR)
@transaction.atomic
def save(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    workspace = MarkingWorkspace.of(request, access, draft_id, lock=True)
    path = _apply(request, workspace, request.POST.get("path", ""))
    if not workspace.valid or workspace.conflicts:
        path = (
            path
            if workspace.problems_at(path, saving=True)
            else next((problem.step_path for problem in workspace.draft.step_problems), path)
        )
        context = {"access": access, "editor": _editor(request, workspace, path)}
        return render(request, "marking/editor.html", context)
    subject = workspace.subject
    marking = workspace.save_marking(endpoint(request))
    messages.success(request, "Saved. Marking students…")
    if subject.question is None:
        return HttpResponseLocation(reverse("question_group", args=[access.assessment.pk, marking.pk]))
    return _render_marking_column(request, access, subject.question, answers=True)


@require_POST
@assessment_member(Role.EDITOR)
@transaction.atomic
def discard(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    workspace = MarkingWorkspace.of(request, access, draft_id, lock=True)
    subject = workspace.subject
    workspace.discard()
    messages.success(request, "Changes discarded.")
    if subject.question is not None:
        return _render_marking_column(request, access, subject.question, answers=True)
    assessment = access.assessment
    if subject.marking is not None:
        return HttpResponseLocation(reverse("question_group", args=[assessment.pk, subject.marking.pk]))
    return HttpResponseLocation(reverse("question_group_new", args=[assessment.pk]))


@require_POST
@assessment_member(Role.EDITOR)
@transaction.atomic
def remove(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    question = access.question(question_id)
    access.assessment.lock()
    if marking := Marking.objects.filter(question=question).first():
        delete_marking(marking)
    notify_change(topic("questions", access.assessment.pk))
    messages.success(request, "Marking removed.")
    return _render_marking_column(request, access, question)


@assessment_member()
def history(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    question = access.question(question_id)
    loaded, questions = loaded_usable_methods(request.user, access.course), access.assessment.question_set()
    history = (
        Marking.history.filter(question_id=question.pk)
        .exclude(history_type="-")
        .select_related("history_user")
        .order_by("-history_date")
    )
    current = Marking.objects.filter(question=question).exists()
    versions = [
        {
            "version": version,
            "summary": saved_summary(version.definition, question.key, loaded, questions),
            "current": current and index == 0,
        }
        for index, version in enumerate(history)
    ]
    context = {"access": access, "question": question, "versions": versions}
    return render(request, "marking/history.html", context)


@require_POST
@assessment_member(Role.EDITOR)
def restore(request: Request, access: AssessmentAccess, question_id: UUID, history_id: int) -> HttpResponse:
    question = access.question(question_id)
    historical = get_object_or_404(Marking.history, history_id=history_id, question_id=question.pk)
    start_draft(MarkingSubject.of(access.assessment, question), request.user, historical.definition)
    messages.success(request, "Restored as your draft. Save it to use it.")
    return HttpResponseLocation(reverse("question", args=[access.assessment.pk, question.pk]))


@require_POST
@assessment_member(Role.EDITOR)
@transaction.atomic
def mark_example(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    """Changes the draft being edited so an answer group's answers get the marks given them."""
    access.assessment.lock()
    question = access.question(question_id)
    subject = MarkingSubject.of(access.assessment, question)
    draft = subject.user_draft(request.user, lock=True)
    if draft is None:
        raise Http404
    workspace = MarkingWorkspace(draft)
    decision = cast("Decision | Literal['cancel']", request.POST.get("decision", ""))
    method = drafted_method(workspace.draft)
    try:
        workspace.draft.mark_by_example(request.POST.getlist("answer"), decision)
    except GradeFlowError as error:
        messages.error(request, str(error))
    else:
        workspace.save()
        _refresh_list_if_method_changed(request, workspace, method)
    context = {
        "access": access,
        "editor": _editor(request, workspace, ""),
        "answers_html": answers_column_html(request, access, subject, workspace, out_of_band=True),
    }
    return render(request, "marking/panel.html", context)


@assessment_member()
@transaction.atomic
def question_group_new(request: Request, access: AssessmentAccess) -> HttpResponse:
    if request.method == "POST":
        if not access.can_edit:
            raise PermissionDenied
        access.assessment.lock()
        definition = _new_marking(request, MarkingSubject.of(access.assessment), request.POST.get("method", ""))
        draft = MarkingDraftRecord.objects.create(
            assessment=access.assessment, user=request.user, definition=definition
        )
        return redirect("question_group_draft", access.assessment.pk, draft.pk)
    offered = choices(methods_for(request.user, access.assessment), offers(request.user, access.course))
    context = {
        "access": access,
        "question_list": presenters.question_list(access, "new"),
        "methods": [*offered.everyday, *offered.saved, *offered.advanced],
    }
    return render(request, "marking/question_group_new.html", context)


@assessment_member(Role.EDITOR)
def question_methods(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    question = access.question(question_id)
    methods = methods_for(request.user, access.assessment, question.key)
    action = reverse("marking_start", args=[access.assessment.pk, question.pk])
    picker_form = PickerForm(action=action, target="#marking-panel", swap="outerHTML")
    sections = sections_of(methods, offers(request.user, access.course))
    return picker(
        request, sections, request.GET.get("selected", ""), picker_form, access.course, question.core_question.type
    )


@assessment_member(Role.EDITOR)
def draft_methods(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    """The method picker for the step at `target` of the draft, which opens the editor at `path` after."""
    workspace = MarkingWorkspace.of(request, access, draft_id)
    args = [access.assessment.pk, workspace.record.pk]
    picker_form = PickerForm(
        action=reverse("draft", args=args),
        target="#marking-panel",
        swap="outerHTML",
        fields={"path": request.GET.get("path", request.GET.get("target", ""))},
    )
    return step_picker(request, workspace.draft, workspace.offers, access.course, picker_form)


@assessment_member(Role.EDITOR)
def question_group_methods(request: Request, access: AssessmentAccess) -> HttpResponse:
    picker_form = PickerForm(action=reverse("question_group_new", args=[access.assessment.pk]))
    sections = sections_of(methods_for(request.user, access.assessment), offers(request.user, access.course))
    return picker(request, sections, request.GET.get("selected", ""), picker_form, access.course)


@assessment_member()
def question_group(request: Request, access: AssessmentAccess, marking_id: UUID) -> HttpResponse:
    marking = _question_group(access, marking_id)
    subject = MarkingSubject.of(access.assessment, marking=marking)
    workspace = draft_workspace(request, access, subject)
    accepted = assessment_checks(access.assessment)
    context: dict[str, Any] = {
        "access": access,
        "question_list": presenters.question_list(access, str(marking.pk), accepted),
        "group": presenters.saved_group(access.assessment, marking, accepted.get(marking)),
        "answers": answers_column_html(request, access, subject, workspace, accepted=accepted),
    }
    if workspace is not None:
        context["editor"] = _editor(request, workspace, request.GET.get("path", ""))
    return render(request, "marking/question_group.html", context)


@require_POST
@assessment_member(Role.EDITOR)
def question_group_edit(request: Request, access: AssessmentAccess, marking_id: UUID) -> HttpResponse:
    marking = _question_group(access, marking_id)
    start_draft(MarkingSubject.of(access.assessment, marking=marking), request.user, marking.definition)
    return redirect("question_group", access.assessment.pk, marking.pk)


@assessment_member(Role.EDITOR)
def question_group_draft(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    workspace = MarkingWorkspace.of(request, access, draft_id, question=None)
    if workspace.record.marking is not None:
        return redirect("question_group", access.assessment.pk, workspace.record.marking.pk)
    context = {
        "access": access,
        "question_list": presenters.question_list(access, str(workspace.record.pk)),
        "editor": _editor(request, workspace, request.GET.get("path", "")),
        "answers": answers_column_html(request, access, workspace.subject, workspace),
    }
    return render(request, "marking/question_group.html", context)


@require_POST
@assessment_member(Role.EDITOR)
@transaction.atomic
def question_group_remove(request: Request, access: AssessmentAccess, marking_id: UUID) -> HttpResponse:
    access.assessment.lock()
    marking = _question_group(access, marking_id)
    marked_keys = sorted(questions_marked(marking, check_for(marking)))
    delete_marking(marking)
    notify_change(topic("questions", access.assessment.pk))
    messages.success(request, f"Removed the question group marking {', '.join(marked_keys) or 'no questions'}.")
    return redirect("questions", access.assessment.pk)


@assessment_member(Role.EDITOR)
def save_as_method(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    marking = get_object_or_404(Marking, question=question_id, question__assessment=access.assessment)
    return _save_as_method(request, access, marking, reverse("method_save", args=[access.assessment.pk, question_id]))


@assessment_member(Role.EDITOR)
def question_group_save_as_method(request: Request, access: AssessmentAccess, marking_id: UUID) -> HttpResponse:
    marking = _question_group(access, marking_id)
    action = reverse("question_group_method_save", args=[access.assessment.pk, marking_id])
    return _save_as_method(request, access, marking, action)


@transaction.atomic
def _save_as_method(request: Request, access: AssessmentAccess, marking: Marking, action: str) -> HttpResponse:
    """A draft of a new method, in the course's library, that marks as the marking does."""
    course = access.course
    form = SaveMethodForm(data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        methods = usable(request.user, course)
        check = check_of(marking.definition, methods)
        if check is None:
            form.add_error(None, "Finish the marking before saving it as a method.")
        else:
            make_draft = partial(MethodDraft.from_marking, check, access.assessment.question_set(), methods=methods)
            try:
                method = libraries.create_method(course.library, request.user, make_draft, **form.cleaned_data)
            except ValidationError as error:
                form.add_error("label", error)
            else:
                return redirect_page(request, reverse("method_page", args=[method.pk]))
    context = {"form": form, "action": action}
    return render(request, "methods/save.html", context, status=422 if form.is_bound else 200)
