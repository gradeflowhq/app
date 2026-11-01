"""The method picker dialog: the methods to choose from, beside the guide to the one selected."""

import dataclasses

from django.http import Http404, HttpResponse
from django.shortcuts import render
from django.utils.http import urlencode
from gradeflow_core import GradeFlowError
from gradeflow_core.editing import MarkingDraft

from gradeflow_app.courses.access import course_of
from gradeflow_app.courses.models import Course
from gradeflow_app.editor.guide import TABS, Section, guide_context, question_group_check, sections_of, several
from gradeflow_app.libraries.collection import Offers, offers, usable
from gradeflow_app.requests import Request


@dataclasses.dataclass(frozen=True, kw_only=True)
class PickerUse:
    """Where the picker's form sends the chosen method: to `action`, as `field`, with any other `fields`; into
    `target` with `swap`, along with what `include` selects, or else as a plain form."""

    action: str
    field: str
    target: str = ""
    swap: str = ""
    include: str = ""
    current: str = ""
    """The method of the check being changed."""
    fields: dict[str, str] = dataclasses.field(default_factory=dict)
    questions: list[str] = dataclasses.field(default_factory=list)
    """For a new check of a question group, the questions it may read, `question` chosen, or none if `several`
    allows a method of several questions; choosing another opens the picker at `reload`."""
    question: str = ""
    several: bool = False
    reload: str = ""


def picker(
    request: Request, sections: list[Section], selected: str, use: PickerUse, course: Course | None, kind: str = ""
) -> HttpResponse:
    """The method picker dialog's body, for marking in `course` a question of type `kind`, if one."""
    refs = [method.ref for section in sections for method, _ in section.entries]
    if not refs:
        raise Http404
    selected = selected if selected in refs else refs[0]
    owners = {method.name: owner for section in sections for method, owner in section.entries if owner}
    context = guide_context(selected, usable(request.user, course), kind, owners)
    asked = {"course": str(course.pk)} if course else {}
    query = f"?{urlencode(asked | ({'kind': kind} if kind else {}))}" if asked or kind else ""
    counts = {key: sum(len(section.entries) for section in sections if section.tab == key) for key in TABS}
    context |= {
        "sections": sections,
        "use": use,
        "query": query,
        "tabs": [(key, label, counts[key]) for key, label in TABS.items()],
        "tab": next(section.tab for section in sections for method, _ in section.entries if method.ref == selected),
    }
    return render(request, "marking/picker.html", context)


def check_picker(
    request: Request, checks: MarkingDraft, offered: Offers, course: Course | None, use: PickerUse, here: str
) -> HttpResponse:
    """The picker, at `here`, for changing the method of the check at `target` (`op=retype`) or adding one there
    (`op=add`). A check added to a question group reads the question chosen with it."""
    target = request.GET.get("target", "")
    retyping = request.GET.get("op") == "retype"
    if retyping and not checks.is_check(target):
        raise Http404
    try:
        position = checks.position(target)
        in_group = None if retyping else question_group_check(checks, target, request.GET.get("question"))
        question = in_group.question if in_group else None
        methods = checks.methods(target, question)
    except GradeFlowError:
        raise Http404 from None
    method = checks.method(target) if retyping else None
    use = dataclasses.replace(
        use,
        current=method.ref if method else "",
        fields={"op": "retype" if retyping else "add", "target": target} | use.fields,
    )
    if in_group:
        use = dataclasses.replace(
            use,
            questions=in_group.questions,
            question=question or "",
            several=several(checks, target),
            reload=f"{here}?{urlencode(use.fields)}",
        )
    kind = checks.questions[asked].type if (asked := question or position.question) else ""
    return picker(request, sections_of(methods, offered), use.current, use, course, kind)


def method(request: Request, ref: str) -> HttpResponse:
    """A method's guide, for the picker's pane, as someone marking in the course `?course=` sees it."""
    if not request.htmx:
        raise Http404
    course = course_of(request)
    context = guide_context(
        ref, usable(request.user, course), request.GET.get("kind", ""), offers(request.user, course).owners
    )
    return render(request, "marking/method_body.html", context)
