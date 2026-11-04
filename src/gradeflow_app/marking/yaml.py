"""Editing a draft marking as YAML, for those who would rather type it, with problems on their lines."""

from typing import Any
from uuid import UUID

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import reverse
from django_htmx.http import HttpResponseClientRefresh
from gradeflow_core import InvalidError
from gradeflow_core.editing import MarkingDraft, dump_yaml

from gradeflow_app.assessments.access import assessment_member
from gradeflow_app.assessments.models import AssessmentAccess
from gradeflow_app.courses.models import Role
from gradeflow_app.editor.presenters import editor
from gradeflow_app.marking.answers.column import answers_column_html
from gradeflow_app.marking.workspace import MarkingWorkspace
from gradeflow_app.requests import Request


@assessment_member(Role.EDITOR)
@transaction.atomic
def draft_yaml(request: Request, access: AssessmentAccess, draft_id: UUID) -> HttpResponse:
    """The draft as YAML; posting saves the marking it reads as, of its question, then shows what else is wrong, if
    anything, with the editor behind brought up to date."""
    workspace = MarkingWorkspace.of(request, access, draft_id, lock=request.method == "POST")
    draft_record = workspace.draft
    text = draft_record.definition
    problems: list[str] = []
    action = reverse("draft_yaml", args=[access.assessment.pk, draft_record.pk])
    context: dict[str, Any] = {"action": action}
    if request.method == "POST":
        text = str(request.POST.get("definition", ""))
        try:
            draft = MarkingDraft.from_yaml(text, workspace.questions, workspace.subject.key, methods=workspace.methods)
        except InvalidError as error:
            problems = [str(problem) for problem in error.problems]
        else:
            problems = [str(problem) for problem in draft.problems]
            draft_record.definition = dump_yaml(draft.data)
            draft_record.save(update_fields=["definition"])
            if not problems:
                return HttpResponseClientRefresh()
            saved = MarkingWorkspace(draft_record)
            context |= {
                "access": access,
                "editor": editor(saved, str(request.POST.get("path", ""))),
                "answers_html": answers_column_html(request, access, saved.subject, saved, out_of_band=True),
                "out_of_band_editor": True,
            }
    context |= {"text": text, "problems": problems}
    return render(request, "marking/yaml.html", context, status=422 if problems else 200)
