from uuid import UUID

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django_htmx.http import HttpResponseClientRefresh
from gradeflow_core.editing import QUESTION_TYPES

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.models import Membership
from gradeflow_app.courses.models import Role
from gradeflow_app.imports.models import answer_to
from gradeflow_app.live.topics import publish, topic
from gradeflow_app.marking.remarks import remark
from gradeflow_app.model_service.endpoints import endpoint
from gradeflow_app.questions.forms import QuestionForm, chosen_type, retyped
from gradeflow_app.questions.models import TYPE_LABELS
from gradeflow_app.requests import Request


@member(Role.EDITOR)
@transaction.atomic
def question_settings(request: Request, membership: Membership, question_id: UUID) -> HttpResponse:
    """A question's type and settings, from the schema the core publishes for each type."""
    if request.method == "POST":
        membership.assessment.lock()
    question = membership.question(question_id)
    current = question.core
    chosen = chosen_type(request.POST.get("question_type") or request.GET.get("question_type", ""), current.type)
    saving = request.method == "POST" and "save" in request.POST
    answers = membership.assessment.students.values_list(answer_to(question.key), flat=True)
    form = QuestionForm(retyped(current, chosen, answers), data=request.POST if saving else None)
    if saving and (changed := form.question()) is not None:
        question.core = changed
        question.save()
        if changed.model_copy(update={"max_marks": current.max_marks}) == current:
            messages.success(request, "Saved.")
        else:
            remark(membership.assessment, [question.key], request.user, endpoint(request))
            messages.success(request, f"Saved. Marking {question.key} again…")
        publish(topic("questions", membership.assessment.pk))
        return HttpResponseClientRefresh()
    return render(
        request,
        "questions/settings.html",
        {
            "membership": membership,
            "question": question,
            "types": TYPE_LABELS.items(),
            "chosen": chosen,
            "summary": QUESTION_TYPES[chosen].summary,
            "form": form,
        },
    )
