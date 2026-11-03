"""The method picker dialog: the methods to choose from, beside the guide to the one selected."""

import dataclasses

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.http import urlencode
from gradeflow_core import GradeFlowError
from gradeflow_core.editing import MarkingDraft

from gradeflow_app.courses.access import course_from_query
from gradeflow_app.courses.models import Course
from gradeflow_app.editor.guide import (
    PICKER_TABS,
    Section,
    allows_group_methods,
    guide_context,
    question_group_check,
    sections_of,
)
from gradeflow_app.libraries.collection import OfferedMethods, offers, usable
from gradeflow_app.requests import Request


@dataclasses.dataclass(frozen=True, kw_only=True)
class PickerForm:
    """Where the picker's form sends the chosen method: to `action`, as `field`, with any other `fields`; into
    `target` with `swap`, along with what `include` selects, or else as a plain form."""

    action: str
    field: str
    target: str = ""
    swap: str = ""
    include: str = ""
    current_method: str = ""
    """The method of the check being changed."""
    fields: dict[str, str] = dataclasses.field(default_factory=dict)
    questions: list[str] = dataclasses.field(default_factory=list)
    """For a new check of a question group, the questions it may read, `question` chosen, or none if
    `allows_group_methods`; choosing another opens the picker at `reload_url`."""
    question: str = ""
    allows_group_methods: bool = False
    reload_url: str = ""


def picker(
    request: Request,
    sections: list[Section],
    selected: str,
    picker_form: PickerForm,
    course: Course | None,
    question_type: str = "",
) -> HttpResponse:
    """The method picker dialog's body, for marking in `course` a question of `question_type`, if one."""
    refs = [method.ref for section in sections for method, _ in section.methods]
    if not refs:
        raise Http404
    selected = selected if selected in refs else refs[0]
    owners = {method.name: owner for section in sections for method, owner in section.methods if owner}
    context = guide_context(selected, usable(request.user, course), question_type, owners)
    params = {"course": str(course.pk)} if course else {}
    if question_type:
        params["question_type"] = question_type
    query_string = f"?{urlencode(params)}" if params else ""
    counts = {key: sum(len(section.methods) for section in sections if section.tab == key) for key in PICKER_TABS}
    context |= {
        "sections": sections,
        "picker_form": picker_form,
        "query_string": query_string,
        "tabs": [(key, label, counts[key]) for key, label in PICKER_TABS.items()],
        "tab": next(section.tab for section in sections for method, _ in section.methods if method.ref == selected),
    }
    return render(request, "marking/picker.html", context)


def check_picker(
    request: Request,
    checks: MarkingDraft,
    offered: OfferedMethods,
    course: Course | None,
    picker_form: PickerForm,
    picker_url: str,
) -> HttpResponse:
    """The picker, at `picker_url`, for changing the method of the check at `target` (`operation=change_method`) or
    adding one there (`operation=add`). A check added to a question group reads the question chosen with it."""
    target = request.GET.get("target", "")
    changing_method = request.GET.get("operation") == "change_method"
    if changing_method and not checks.is_check(target):
        raise Http404
    try:
        position = checks.position(target)
        group_check = None if changing_method else question_group_check(checks, target, request.GET.get("question"))
        question = group_check.question if group_check else None
        methods = checks.methods(target, question)
    except GradeFlowError:
        raise Http404 from None
    method = checks.method(target) if changing_method else None
    picker_form = dataclasses.replace(
        picker_form,
        current_method=method.ref if method else "",
        fields={"operation": "change_method" if changing_method else "add", "target": target} | picker_form.fields,
    )
    if group_check:
        picker_form = dataclasses.replace(
            picker_form,
            questions=group_check.questions,
            question=question or "",
            allows_group_methods=allows_group_methods(checks, target),
            reload_url=f"{picker_url}?{urlencode(picker_form.fields)}",
        )
    question_key = question or position.question
    question_type = checks.questions[question_key].type if question_key else ""
    return picker(
        request, sections_of(methods, offered), picker_form.current_method, picker_form, course, question_type
    )


def method_guide(request: Request, ref: str) -> HttpResponse:
    """A method's guide, for the picker's pane, as someone marking in the course `?course=` sees it."""
    if not request.htmx:
        raise Http404
    course = course_from_query(request)
    context = guide_context(
        ref, usable(request.user, course), request.GET.get("question_type", ""), offers(request.user, course).owners
    )
    return render(request, "marking/method_body.html", context)
