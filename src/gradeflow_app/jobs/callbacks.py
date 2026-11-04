"""The endpoints sandbox jobs call: their input, and events as they run. Both are signed with the job's secret.

Jobs send results as `marked` batches or `compared` JSON pieces to keep each request under the app's size limit, and
other events their own task's handler applies; `finished` follows the last.
"""

import hashlib
import hmac
import json
import logging
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

_LOG = logging.getLogger(__name__)


def _has_valid_signature(job: Job, request: HttpRequest, body: bytes) -> bool:
    expected = hmac.new(job.secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, request.headers.get("X-GradeFlow-Signature", ""))


@login_not_required
@require_GET
def job_input(request: HttpRequest, job_id: UUID) -> HttpResponse:
    job = get_object_or_404(Job, pk=job_id, status__in=Job.ACTIVE)
    if not _has_valid_signature(job, request, b"input") or job.input is None:
        return HttpResponseForbidden()
    return JsonResponse(job.input)


@csrf_exempt
@login_not_required
@require_POST
def job_callback(request: HttpRequest, job_id: UUID) -> HttpResponse:
    job = get_object_or_404(Job.objects.defer("input", "result", "marked_with"), pk=job_id)
    if not _has_valid_signature(job, request, request.body):
        _LOG.warning("Refused a callback for job %s: its signature doesn't match", job.pk)
        return HttpResponseForbidden()
    try:
        event = json.loads(request.body)
        if not isinstance(event, dict):
            raise TypeError("A job event is a JSON object.")
        _apply_event(job, event)
    except KeyError, TypeError, ValueError:
        _LOG.warning("Refused a callback for job %s", job.pk, exc_info=True)
        return HttpResponse(status=400)
    return HttpResponse(status=204)


def _apply_event(job: Job, event: dict[str, Any]) -> None:
    task = handler(job).task
    match event.get("event"):
        case "started":
            events.apply_started(job)
        case "compared" if task == "compare":
            events.apply_comparison_part(job, event)
        case "marked" if task == "mark":
            events.add_marked_batch(job, event["submissions"])
        case "progress" if task == "mark":
            events.record_progress(job, event)
        case "tested" if task == "test":
            events.store_test_results(job, event["results"])
        case "finished":
            queue.end(job)
        case "failed":
            queue.end(job, str(event.get("error", "")))
        case _ if handler(job).apply_event is not None:
            events.apply_task_event(job, event)
        case _:
            raise ValueError("Unknown job event.")
