import json
from dataclasses import dataclass

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.html import format_html_join
from django.views.decorators.http import require_GET, require_POST

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import Assessment, AssessmentAccess
from gradeflow_app.courses.access import course_member
from gradeflow_app.courses.models import Member, Role
from gradeflow_app.publish.forms import AssessmentCanvasForm, CourseCanvasForm, SentMarksForm
from gradeflow_app.publish.models import AssessmentCanvas, CourseCanvas, MarksMode, SentMarks
from gradeflow_app.requests import Request
from gradeflow_app.results.forms import download_form
from gradeflow_app.results.marks import Results, StudentMarks, assessment_results
from gradeflow_app.ui.forms import first_error
from gradeflow_app.ui.fragments import fragment_of
from gradeflow_app.ui.templatetags.ui import marks


def _canvas_config(course: CourseCanvas, **assessment_fields: object) -> str:
    """What canvas.js needs to reach the course's Canvas course, as `data-config`."""
    return json.dumps(
        {
            "proxy": settings.GRADEFLOW_CORS_PROXY_URL,
            "course": course.canvas_course,
            "courseName": course.canvas_course_name,
            "assignmentGroup": course.canvas_assignment_group,
            "assignmentGroupName": course.canvas_assignment_group_name,
            **assessment_fields,
        }
    )


@course_member(Role.EDITOR)
def canvas_settings(request: Request, member: Member) -> HttpResponse:
    form = CourseCanvasForm(request.POST or None, instance=CourseCanvas.of(member.course))
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Saved.")
        return redirect("canvas_settings", member.course_id)
    return render(
        request,
        "publish/canvas.html",
        {
            "member": member,
            "form": form,
            "canvas_available": bool(settings.GRADEFLOW_CORS_PROXY_URL),
            "config": _canvas_config(form.instance),
        },
    )


@assessment_member()
def send_marks(request: Request, membership: AssessmentAccess) -> HttpResponse:
    assessment = membership.assessment
    canvas_available = bool(settings.GRADEFLOW_CORS_PROXY_URL)
    assessment_canvas = AssessmentCanvas.of(assessment)
    course_canvas = CourseCanvas.of(membership.course)
    context: dict[str, object] = {
        "membership": membership,
        "canvas_available": canvas_available,
        "course_canvas": course_canvas,
        "sending": effective_sending(assessment_canvas, course_canvas),
        "sent_marks": _sent_marks(assessment),
        "student_count": assessment.students.count(),
        "download_form": download_form(),
    }
    if canvas_available and course_canvas.canvas_course and membership.can_edit:
        context |= {
            "form": AssessmentCanvasForm(instance=assessment_canvas),
            "canvas_rows": _canvas_rows(membership),
            "config": _canvas_config(
                course_canvas,
                assessment=str(assessment.pk),
                name=assessment.name,
                assignment=assessment_canvas.canvas_assignment,
                sentUrl=reverse("publish_record", args=[assessment.pk]),
            ),
        }
    return render(request, "publish/publish.html", context)


@require_GET
@assessment_member(Role.EDITOR)
@fragment_of("publish")
def canvas_rows(request: Request, membership: AssessmentAccess) -> HttpResponse:
    return render(
        request,
        "publish/publish.html#canvas_rows",
        {"membership": membership, "canvas_rows": _canvas_rows(membership)},
    )


def _canvas_rows(membership: AssessmentAccess) -> list[dict[str, object]]:
    """Each student's encrypted ID, total and comment, for the browser to send to Canvas."""
    results = assessment_results(membership.assessment)
    return [
        {
            "studentId": row.student.encrypted_id,
            "marks": row.total,
            "maxMarks": results.max_marks,
            "comment": _comment(results, row),
        }
        for row in results.rows
    ]


def _comment(results: Results, row: StudentMarks) -> str:
    """The row's marking report as an HTML gradebook comment: each question's marks in bold, then its feedback."""
    lines = results.marked_submission(row).report()
    return format_html_join(
        "\n", "<strong>{}</strong>{}", ((line.heading, f"\n{line.feedback}" if line.feedback else "") for line in lines)
    )


@require_GET
@assessment_member()
@fragment_of("publish")
def comment(request: Request, membership: AssessmentAccess) -> HttpResponse:
    """A student's comment as it's sent with their marks, a page per student."""
    results = assessment_results(membership.assessment)
    if not results.rows:
        raise Http404
    page = Paginator(results.rows, 1).get_page(request.GET.get("page"))
    (row,) = page.object_list
    return render(
        request,
        "publish/comment.html",
        {"membership": membership, "page": page, "student": row.student, "comment": _comment(results, row)},
    )


@dataclass(frozen=True)
class Sending:
    """How an assessment's marks go to Canvas, and how many of its course's settings it overrides."""

    mode: str
    rounding: float | None
    include_feedback: bool
    override_count: int

    @property
    def summary(self) -> str:
        """How the marks go, such as "Marks, rounded to 0.5, with feedback"."""
        return ", ".join(
            (
                "Percentages" if self.mode == MarksMode.PERCENT else "Marks",
                f"rounded to {marks(self.rounding)}" if self.rounding else "not rounded",
                "with feedback" if self.include_feedback else "without feedback",
            )
        )


def effective_sending(assessment: AssessmentCanvas, course: CourseCanvas) -> Sending:
    mode, rounding, feedback = assessment.marks_mode, assessment.rounding, assessment.include_feedback
    return Sending(
        mode=mode or course.marks_mode,
        rounding=course.rounding if rounding is None else rounding,
        include_feedback=course.include_feedback if feedback is None else feedback,
        override_count=sum(value not in ("", None) for value in (mode, rounding, feedback)),
    )


@require_POST
@assessment_member(Role.EDITOR)
def save_sending(request: Request, membership: AssessmentAccess) -> HttpResponse:
    """Saves the assessment's assignment and its overrides; answers with how its marks are now sent."""
    form = AssessmentCanvasForm(request.POST, instance=AssessmentCanvas.of(membership.assessment))
    if not form.is_valid():
        messages.error(request, first_error(form))
        return HttpResponse(status=422)
    sending = effective_sending(form.save(), CourseCanvas.of(membership.course))
    return render(
        request,
        "publish/publish.html#send_settings",
        {"membership": membership, "sending": sending, "out_of_band": True},
    )


@require_POST
@assessment_member(Role.EDITOR)
def record_sent(request: Request, membership: AssessmentAccess) -> HttpResponse:
    form = SentMarksForm(request.POST, instance=SentMarks(assessment=membership.assessment, sent_by=request.user))
    if not form.is_valid():
        messages.error(request, "Sent, but GradeFlow couldn't record it.")
        return HttpResponse(status=400)
    form.save()
    return render(
        request,
        "publish/publish.html#sent_marks",
        {"membership": membership, "sent_marks": _sent_marks(membership.assessment)},
    )


def _sent_marks(assessment: Assessment) -> list[SentMarks]:
    return list(assessment.sent_marks.select_related("sent_by")[:5])
