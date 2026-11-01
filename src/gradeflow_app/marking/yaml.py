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

from gradeflow_app.assessments.access import member
from gradeflow_app.assessments.models import Membership
from gradeflow_app.courses.models import Role
from gradeflow_app.editor.presenters import editor
from gradeflow_app.marking.answers.column import column
from gradeflow_app.marking.workspace import Workspace
from gradeflow_app.requests import Request


@member(Role.EDITOR)
@transaction.atomic
def draft_yaml(request: Request, membership: Membership, draft: UUID) -> HttpResponse:
    """The draft as YAML; posting saves the marking it reads as, of its question, then shows what else is wrong, if
    anything, with the editor behind brought up to date."""
    workspace = Workspace.of(request, membership, draft, lock=request.method == "POST")
    found = workspace.draft
    text = found.definition
    wrong: list[str] = []
    action = reverse("draft_yaml", args=[membership.assessment.pk, found.pk])
    context: dict[str, Any] = {"action": action}
    if request.method == "POST":
        text = str(request.POST.get("definition", ""))
        try:
            read = MarkingDraft.from_yaml(text, workspace.questions, workspace.subject.key, methods=workspace.methods)
        except InvalidError as error:
            wrong = [str(problem) for problem in error.problems]
        else:
            wrong = [str(problem) for problem in read.problems]
            found.definition = dump_yaml(read.data)
            found.save(update_fields=["definition"])
            if not wrong:
                return HttpResponseClientRefresh()
            saved = Workspace(found)
            context |= {
                "membership": membership,
                "editor": editor(saved, str(request.POST.get("path", ""))),
                "answers_html": column(request, membership, saved.subject, saved, oob=True),
                "oob_editor": True,
            }
    context |= {"text": text, "problems": wrong}
    return render(request, "marking/yaml.html", context, status=422 if wrong else 200)
