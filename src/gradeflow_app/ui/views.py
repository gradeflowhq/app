from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from gradeflow_app.requests import Request


@require_POST
def markdown_preview(request: Request) -> HttpResponse:
    """Markdown someone is writing, as pages show it."""
    return render(request, "forms/markdown_preview.html", {"text": request.POST.get("text", "")})
