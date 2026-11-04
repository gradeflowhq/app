from uuid import UUID

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django_htmx.http import HttpResponseClientRefresh
from gradeflow_core.editing import QUESTION_TYPES

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.courses.models import Role
from gradeflow_app.imports.models import answer_expression
from gradeflow_app.live.topics import notify_change, topic
from gradeflow_app.marking.remarks import remark
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.questions.forms import QuestionForm, question_type_named, retyped
from gradeflow_app.questions.models import QUESTION_TYPE_LABELS
from gradeflow_app.requests import Request


@assessment_member(Role.EDITOR)
@transaction.atomic
def question_settings(request: Request, access: AssessmentAccess, question_id: UUID) -> HttpResponse:
    """A question's type and settings, from the schema the core publishes for each type."""
    if request.method == "POST":
        access.assessment.lock()
    question = access.question(question_id)
    saved_question = question.core_question
    question_type = question_type_named(
        request.POST.get("question_type") or request.GET.get("question_type", ""), saved_question.type
    )
    is_saving = request.method == "POST" and "save" in request.POST
    answers = access.assessment.students.values_list(answer_expression(question.key), flat=True)
    form = QuestionForm(retyped(saved_question, question_type, answers), data=request.POST if is_saving else None)
    if is_saving and (posted_question := form.posted_question()) is not None:
        question.core_question = posted_question
        question.save()
        if posted_question.model_copy(update={"max_marks": saved_question.max_marks}) == saved_question:
            messages.success(request, "Saved.")
        else:
            remark(access.assessment, [question.key], request.user, endpoint(request))
            messages.success(request, f"Saved. Marking {question.key} again…")
        notify_change(topic("questions", access.assessment.pk))
        return HttpResponseClientRefresh()
    return render(
        request,
        "questions/settings.html",
        {
            "access": access,
            "question": question,
            "types": QUESTION_TYPE_LABELS.items(),
            "question_type": question_type,
            "summary": QUESTION_TYPES[question_type].summary,
            "form": form,
        },
    )
