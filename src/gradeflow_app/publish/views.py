import json
from dataclasses import dataclass
from html import escape

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.models import Assessment, Membership
from gradeflow_app.courses.access import course_member
from gradeflow_app.courses.models import Member, Role
from gradeflow_app.publish.forms import CanvasCourseForm, PublicationForm, SendingForm
from gradeflow_app.publish.models import AssessmentCanvas, CourseCanvas, Mode, Publication
from gradeflow_app.requests import Request
from gradeflow_app.results.forms import download_form
from gradeflow_app.results.marks import Results, Row, results
from gradeflow_app.ui.forms import first_error
from gradeflow_app.ui.fragments import part_of
from gradeflow_app.ui.templatetags.ui import marks


def _config(course: CourseCanvas, **more: object) -> str:
    """What canvas.js needs to reach the course's Canvas course, as `data-config`."""
    return json.dumps(
        {
            "proxy": settings.GRADEFLOW_CORS_PROXY_URL,
            "course": course.canvas_course,
            "courseName": course.canvas_course_name,
            "group": course.canvas_group,
            "groupName": course.canvas_group_name,
            **more,
        }
    )


@course_member(Role.EDITOR)
def canvas(request: Request, member: Member) -> HttpResponse:
    form = CanvasCourseForm(request.POST or None, instance=CourseCanvas.of(member.course))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect("canvas", member.course_id)
    return render(
        request,
        "publish/canvas.html",
        {
            "member": member,
            "form": form,
            "available": bool(settings.GRADEFLOW_CORS_PROXY_URL),
            "config": _config(form.instance),
        },
    )


@member()
def publish(request: Request, membership: Membership) -> HttpResponse:
    assessment = membership.assessment
    canvas = bool(settings.GRADEFLOW_CORS_PROXY_URL)
    assessment_canvas = AssessmentCanvas.of(assessment)
    course_canvas = CourseCanvas.of(membership.course)
    context: dict[str, object] = {
        "membership": membership,
        "canvas": canvas,
        "course_canvas": course_canvas,
        "sending": sending(assessment_canvas, course_canvas),
        "publications": _publications(assessment),
        "students": assessment.students.count(),
        "download": download_form(),
    }
    if canvas and course_canvas.canvas_course and membership.can_edit:
        context |= {
            "form": SendingForm(instance=assessment_canvas),
            "rows": _rows(membership),
            "config": _config(
                course_canvas,
                assessment=str(assessment.pk),
                name=assessment.name,
                assignment=assessment_canvas.canvas_assignment,
                sentUrl=reverse("publish_sent", args=[assessment.pk]),
            ),
        }
    return render(request, "publish/publish.html", context)


@require_GET
@member(Role.EDITOR)
@part_of("publish")
def rows(request: Request, membership: Membership) -> HttpResponse:
    return render(request, "publish/publish.html#rows", {"membership": membership, "rows": _rows(membership)})


def _rows(membership: Membership) -> list[dict[str, object]]:
    """Each student's encrypted ID, total and comment, for the browser to send to Canvas."""
    assessment_results = results(membership.assessment)
    return [
        {
            "studentId": row.student.key,
            "marks": row.total,
            "maxMarks": assessment_results.max_marks,
            "comment": _comment(assessment_results, row),
        }
        for row in assessment_results.rows
    ]


def _comment(assessment_results: Results, row: Row) -> str:
    """The row's marking report as an HTML gradebook comment: each question's marks in bold, then its feedback."""
    return "\n".join(
        f"<strong>{escape(line.heading)}</strong>" + (f"\n{escape(line.feedback)}" if line.feedback else "")
        for line in assessment_results.marked(row).report()
    )


@require_GET
@member()
@part_of("publish")
def comment(request: Request, membership: Membership) -> HttpResponse:
    """A student's comment as it's sent with their marks, a page per student."""
    assessment_results = results(membership.assessment)
    if not assessment_results.rows:
        raise Http404
    page = Paginator(assessment_results.rows, 1).get_page(request.GET.get("page"))
    (row,) = page.object_list
    return render(
        request,
        "publish/comment.html",
        {"membership": membership, "page": page, "student": row.student, "comment": _comment(assessment_results, row)},
    )


@dataclass(frozen=True)
class Sending:
    """How an assessment's marks go to Canvas, and how many of its course's settings it overrides."""

    mode: str
    rounding: float | None
    include_feedback: bool
    overrides: int

    @property
    def summary(self) -> str:
        """How the marks go, such as "Marks, rounded to 0.5, with feedback"."""
        return ", ".join(
            (
                "Percentages" if self.mode == Mode.PERCENT else "Marks",
                f"rounded to {marks(self.rounding)}" if self.rounding else "not rounded",
                "with feedback" if self.include_feedback else "without feedback",
            )
        )


def sending(assessment: AssessmentCanvas, course: CourseCanvas) -> Sending:
    mode, rounding, feedback = assessment.marks_mode, assessment.rounding, assessment.include_feedback
    return Sending(
        mode=mode or course.marks_mode,
        rounding=course.rounding if rounding is None else rounding,
        include_feedback=course.include_feedback if feedback is None else feedback,
        overrides=sum(value not in ("", None) for value in (mode, rounding, feedback)),
    )


@require_POST
@member(Role.EDITOR)
def save_sending(request: Request, membership: Membership) -> HttpResponse:
    """Saves the assessment's assignment and its overrides; answers with how its marks are now sent."""
    form = SendingForm(request.POST, instance=AssessmentCanvas.of(membership.assessment))
    if not form.is_valid():
        messages.error(request, first_error(form))
        return HttpResponse(status=422)
    saved = sending(form.save(), CourseCanvas.of(membership.course))
    return render(request, "publish/publish.html#sending", {"membership": membership, "sending": saved, "oob": True})


@require_POST
@member(Role.EDITOR)
def sent(request: Request, membership: Membership) -> HttpResponse:
    form = PublicationForm(request.POST, instance=Publication(assessment=membership.assessment, sent_by=request.user))
    if not form.is_valid():
        messages.error(request, "Sent, but GradeFlow couldn't record it.")
        return HttpResponse(status=400)
    form.save()
    return render(
        request,
        "publish/publish.html#publications",
        {"membership": membership, "publications": _publications(membership.assessment)},
    )


def _publications(assessment: Assessment) -> list[Publication]:
    return list(assessment.publications.select_related("sent_by")[:5])
