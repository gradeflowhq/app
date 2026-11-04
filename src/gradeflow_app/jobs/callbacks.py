"""The endpoints runs call: their input, and events as they go. Both are signed with the run's secret.

A run sends `progress` and `marked` events as it goes, and its task's own events, then `finished` with its outcome or
`failed` with why.
"""

import hashlib
import hmac
import json
import logging
from typing import Any

from django.contrib.auth.decorators import login_not_required
from django.http import Http404, HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from gradeflow_app.jobs import runs
from gradeflow_app.jobs.handlers import handler, parse_run
from gradeflow_app.jobs.runner import Run

_LOG = logging.getLogger(__name__)


def _run(run_id: str) -> Run:
    if (run := parse_run(run_id)) is None:
        raise Http404
    return run


def _has_valid_signature(run: Run, request: HttpRequest, body: bytes) -> bool:
    expected = hmac.new(runs.secret(run).encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, request.headers.get("X-GradeFlow-Signature", ""))


@login_not_required
@require_GET
def job_input(request: HttpRequest, run_id: str) -> HttpResponse:
    run = _run(run_id)
    if not _has_valid_signature(run, request, b"input"):
        return HttpResponseForbidden()
    if (run_input := handler(run).input(run)) is None:
        return HttpResponse(status=410)
    return JsonResponse(run_input.model_dump(mode="json"))


@csrf_exempt
@login_not_required
@require_POST
def job_callback(request: HttpRequest, run_id: str) -> HttpResponse:
    run = _run(run_id)
    body = request.read()  # `request.body` stops at DATA_UPLOAD_MAX_MEMORY_SIZE, and an outcome can be larger
    if not _has_valid_signature(run, request, body):
        _LOG.warning("Refused a callback for %s: its signature doesn't match", run_id)
        return HttpResponseForbidden()
    try:
        event = json.loads(body)
        if not isinstance(event, dict):
            raise TypeError("A job event is a JSON object.")
        _apply_event(run, event)
    except KeyError, TypeError, ValueError:
        _LOG.warning("Refused a callback for %s", run_id, exc_info=True)
        return HttpResponse(status=400)
    return HttpResponse(status=204)


def _apply_event(run: Run, event: dict[str, Any]) -> None:
    match event.get("event"):
        case "progress":
            runs.record_progress(
                run, progress_text=f"{event['step']}: {int(event['completed'])} of {int(event['total'])}"
            )
        case "marked":
            runs.record_progress(run, progress=int(event["count"]) / int(event["total"]))
        case "finished":
            runs.end(run, outcome=event.get("outcome"))
        case "failed":
            runs.end(run, error=str(event["error"]))
        case _:
            runs.apply_event(run, event)
