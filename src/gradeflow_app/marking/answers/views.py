from uuid import UUID

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.template.defaultfilters import pluralize
from django.views.decorators.http import require_POST

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.models import Membership
from gradeflow_app.courses.models import Role
from gradeflow_app.live.topics import publish, topic
from gradeflow_app.marking.answers.column import answers_context, group_by_meaning
from gradeflow_app.requests import Request
from gradeflow_app.results.forms import AdjustmentForm
from gradeflow_app.results.models import Adjustment
from gradeflow_app.results.views import student_page


@member()
def answers(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    question = membership.question(question_id)
    return render(request, "results/answers.html#answers", answers_context(request, membership, question))


@require_POST
@member()
def answer_grouping(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    question = membership.question(question_id)
    group_by_meaning(request, membership, question)
    return render(request, "results/answers.html#answers", answers_context(request, membership, question))


@require_POST
@member(Role.EDITOR)
def adjust(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    """Adjust, or clear the adjustments of, the students listed: an answer group, or one student."""
    question = membership.question(question_id)
    students = list(membership.assessment.students.filter(pk__in=request.POST.getlist("student")))
    if request.POST.get("action") == "clear":
        cleared, _ = Adjustment.objects.filter(question=question, student__in=students).delete()
        messages.success(request, f"Cleared {cleared} adjustment{pluralize(cleared)}.")
    else:
        form = AdjustmentForm(request.POST, question.core.max_marks)
        if not form.is_valid():
            messages.error(request, str(form.errors["marks"][0]))
            return HttpResponse(status=422)
        with transaction.atomic():
            for student in students:
                Adjustment.objects.update_or_create(
                    student=student,
                    question=question,
                    defaults={"marks": form.cleaned_data["marks"], "feedback": form.cleaned_data["feedback"]},
                )
        messages.success(request, f"Adjusted {len(students)} student{pluralize(len(students))}.")
    publish(topic("marks", membership.assessment.pk))
    if request.POST.get("then") == "student" and students:
        return student_page(request, membership, students[0])
    return render(request, "results/answers.html#answers", answers_context(request, membership, question))
