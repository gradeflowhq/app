"""The endpoints sandbox jobs call: their input, and events as they run. Both are signed with the job's secret.

Jobs send results as `marked` batches or `compared` JSON pieces to keep each request under the app's size limit;
`finished` follows the last.
"""

import hashlib
import hmac
import json
from typing import Any
from uuid import UUID

from django.contrib.auth.decorators import login_not_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from gradeflow_app.jobs import events, queue
from gradeflow_app.jobs.handlers import handler
from gradeflow_app.jobs.models import Job


def _signed(job: Job, request: HttpRequest, body: bytes) -> bool:
    expected = hmac.new(job.secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, request.headers.get("X-GradeFlow-Signature", ""))


@login_not_required
@require_GET
def job_input(request: HttpRequest, job_id: UUID) -> HttpResponse:
    job = get_object_or_404(Job, pk=job_id, status__in=Job.ACTIVE)
    if not _signed(job, request, b"input") or job.input is None:
        return HttpResponseForbidden()
    return JsonResponse(job.input)


@csrf_exempt
@login_not_required
@require_POST
def job_callback(request: HttpRequest, job_id: UUID) -> HttpResponse:
    job = get_object_or_404(Job, pk=job_id)
    if not _signed(job, request, request.body):
        return HttpResponseForbidden()
    try:
        event = json.loads(request.body)
    except ValueError, UnicodeDecodeError:
        return HttpResponse(status=400)
    if not isinstance(event, dict):
        return HttpResponse(status=400)
    try:
        _receive(job, event)
    except KeyError, TypeError, ValueError:
        return HttpResponse(status=400)
    return HttpResponse(status=204)


def _receive(job: Job, event: dict[str, Any]) -> None:
    task = handler(job).task
    match event.get("event"):
        case "started":
            events.started(job)
        case "compared" if task == "compare":
            events.compared(job, event)
        case "marked" if task == "mark":
            events.marked(job, event["submissions"])
        case "tested" if task == "test":
            events.tested(job, event["results"])
        case "progress" if task == "mark":
            events.progressed(job, event)
        case "chosen" if task == "choose":
            events.chosen(job, event["choice"])
        case "tried" if task == "try":
            events.tried(job, event["trials"])
        case "finished":
            queue.finish(job)
        case "failed":
            queue.finish(job, str(event.get("error", "")))
        case _:
            raise ValueError("Unknown job event.")
