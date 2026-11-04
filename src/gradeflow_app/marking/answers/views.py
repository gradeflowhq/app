from uuid import UUID

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.template.defaultfilters import pluralize
from django.views.decorators.http import require_POST
from simple_history.utils import bulk_create_with_history, bulk_update_with_history

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.courses.models import Role
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.marking.answers.column import answers_context, draft_workspace, group_by_meaning
from gradeflow_app.marking.models import MarkingSubject
from gradeflow_app.questions.models import Question
from gradeflow_app.requests import Request
from gradeflow_app.results.forms import AdjustmentForm
from gradeflow_app.results.models import Adjustment
from gradeflow_app.results.views import student_page


def _answers(request: Request, access: AssessmentAccess, question: Question) -> HttpResponse:
    subject = MarkingSubject.of(access.assessment, question)
    context = answers_context(request, access, subject, draft_workspace(request, access, subject))
    return render(request, "results/answers.html#answers", context)


@assessment_member()
def answers(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    question = access.question(question_id)
    return _answers(request, access, question)


@require_POST
@assessment_member()
def answer_grouping(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    question = access.question(question_id)
    group_by_meaning(request, access, question)
    return _answers(request, access, question)


@require_POST
@assessment_member(Role.EDITOR)
def adjust(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    """Adjust, or clear the adjustments of, the students listed: an answer group, or one student."""
    question = access.question(question_id)
    students = list(access.assessment.students.filter(pk__in=request.POST.getlist("student")))
    if request.POST.get("action") == "clear":
        cleared, _ = Adjustment.objects.filter(question=question, student__in=students).delete()
        messages.success(request, f"Cleared {cleared} adjustment{pluralize(cleared)}.")
    else:
        form = AdjustmentForm(request.POST, question.core_question.max_marks)
        if not form.is_valid():
            messages.error(request, str(form.errors["marks"][0]))
            return HttpResponse(status=422)
        marks, feedback = form.cleaned_data["marks"], form.cleaned_data["feedback"]
        with transaction.atomic():
            access.assessment.lock()
            existing = list(Adjustment.objects.select_for_update().filter(question=question, student__in=students))
            for adjustment in existing:
                adjustment.marks, adjustment.feedback = marks, feedback
            bulk_update_with_history(existing, Adjustment, ["marks", "feedback"])
            adjusted = {adjustment.student_id for adjustment in existing}
            new = [
                Adjustment(student=student, question=question, marks=marks, feedback=feedback)
                for student in students
                if student.pk not in adjusted
            ]
            bulk_create_with_history(new, Adjustment)
        messages.success(request, f"Adjusted {len(students)} student{pluralize(len(students))}.")
    notify_change(topic("marks", access.assessment.pk))
    if request.POST.get("return_to") == "student" and students:
        return student_page(request, access, students[0])
    return _answers(request, access, question)
