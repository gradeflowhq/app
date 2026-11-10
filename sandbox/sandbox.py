"""Runs one GradeFlow job: marks submissions or test answers with the core, compares answers for grouping by
meaning, or tries a model endpoint's models.

Usage: sandbox.py mark|compare|test|choose|trial. It fetches its input, calls back as it goes and ends with its
outcome. After reporting a failure it exits normally, so it isn't run again.
"""

import ctypes
import dataclasses
import hashlib
import hmac
import json
import os
import random
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

JOB_URL = os.environ["GRADEFLOW_JOB_URL"]
JOB_SECRET = os.environ["GRADEFLOW_JOB_SECRET"].encode()
MODEL_ENDPOINT = os.environ.get("GRADEFLOW_MODEL_ENDPOINT")
"""The model endpoint the job asks language models at, with its token, as JSON; unset if it asks none."""
MAX_SECONDS_BETWEEN_CALLBACKS = 1
"""Longest wait between callbacks with progress."""
CHUNK_SIZE = 60
"""Students marked between callbacks when a method asks a language model about many answers at once."""
CALLBACK_ATTEMPTS = 6
"""Tries for each callback, spread over up to a minute, so jobs outlast the app restarting. The app takes each event
once, however often it's sent."""
PR_SET_DUMPABLE = 4
CLIENT = httpx.Client(timeout=120)


def signature(body: bytes) -> str:
    return hmac.new(JOB_SECRET, body, hashlib.sha256).hexdigest()


def call_back(event: dict[str, Any]) -> None:
    """Sends `event`, trying again after a while when the app can't be reached, fails or is busy."""
    body = json.dumps(event).encode()
    headers = {"Content-Type": "application/json", "X-GradeFlow-Signature": signature(body)}
    for attempt in range(1, CALLBACK_ATTEMPTS + 1):
        try:
            response = CLIENT.post(JOB_URL + "callback", content=body, headers=headers)
            if attempt == CALLBACK_ATTEMPTS or not (response.is_server_error or response.status_code == 429):
                response.raise_for_status()
                return
        except httpx.TransportError:
            if attempt == CALLBACK_ATTEMPTS:
                raise
        time.sleep(random.uniform(0, 2**attempt))


def model_endpoint() -> Any:
    from gradeflow_core import ModelEndpoint

    return ModelEndpoint.model_validate_json(MODEL_ENDPOINT) if MODEL_ENDPOINT else None


class Throttle:
    """Lets a callback through about once a second, and always the last."""

    def __init__(self) -> None:
        self.last = 0.0

    def allows(self, completed: int, total: int) -> bool:
        now = time.monotonic()
        if completed < total and now - self.last < MAX_SECONDS_BETWEEN_CALLBACKS:
            return False
        self.last = now
        return True


STEP_THROTTLE = Throttle()


def report_progress(progress: Any) -> None:
    """Says how far a step that waits on a language model has got."""
    if STEP_THROTTLE.allows(progress.completed, progress.total):
        call_back({"event": "progress", **dataclasses.asdict(progress)})


def mark(job: dict[str, Any]) -> list[Any]:
    from gradeflow_core import BUILTINS, MarkScheme, QuestionSet, Submission
    from gradeflow_core import mark as mark_submissions

    submissions = [Submission.model_validate(raw) for raw in job["submissions"]]
    marked = mark_submissions(
        QuestionSet.model_validate(job["questions"]),
        MarkScheme.load(job["markings"], methods=BUILTINS.with_files(job["methods"])),
        submissions,
        model_endpoint=model_endpoint(),
        on_progress=report_progress,
        chunk_size=CHUNK_SIZE,
    )
    outcome, throttle = [], Throttle()
    for submission in marked:
        outcome.append(submission.model_dump(mode="json"))
        if throttle.allows(len(outcome), len(submissions)):
            call_back({"event": "marked", "count": len(outcome), "total": len(submissions)})
    return outcome


def compare(job: dict[str, Any]) -> Any:
    from gradeflow_core import compare_answers

    return compare_answers(job["texts"], similarity="meaning").model_dump(mode="json")


def test(job: dict[str, Any]) -> list[Any]:
    from gradeflow_core import BUILTINS, QuestionSet
    from gradeflow_core.editing import ExampleRow, mark_rows

    results = mark_rows(
        job["method"],
        methods=BUILTINS.with_files(job["methods"]),
        questions=QuestionSet.model_validate(job["questions"]),
        question=job["question"],
        settings=job["settings"],
        rows=[ExampleRow.model_validate(row) for row in job["rows"]],
        model_endpoint=model_endpoint(),
    )
    return [result.model_dump(mode="json") for result in results]


def choose(job: dict[str, Any]) -> None:
    from gradeflow_core import ModelTrial, try_models

    def report_start(model: str, role: str) -> None:
        call_back({"event": "trying", "model": model, "role": role})

    tried = [ModelTrial.model_validate(trial) for trial in job["tried"]]
    for model_trial in try_models(model_endpoint(), tried, on_start=report_start):
        call_back({"event": "tried", "trial": model_trial.model_dump(mode="json")})


def trial(job: dict[str, Any]) -> None:
    from gradeflow_core.editing import try_model

    endpoint = model_endpoint()
    for request in job["trials"]:
        model_trial = try_model(endpoint, request["role"], request["model"])
        call_back({"event": "tried", "trial": model_trial.model_dump(mode="json")})


TASKS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "mark": mark,
    "compare": compare,
    "test": test,
    "choose": choose,
    "trial": trial,
}
"""Each task by name, returning the job's outcome."""


def hide_secret_from_job_code() -> None:
    """Keeps code the job runs, as the same user, from reading the job's secret from this process's memory or
    environment."""
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")


def main(task: str) -> int:
    hide_secret_from_job_code()
    response = CLIENT.get(JOB_URL + "input", headers={"X-GradeFlow-Signature": signature(b"input")})
    if response.status_code == 410:
        return 0  # out of date
    response.raise_for_status()
    try:
        outcome = TASKS[task](response.json())
    except Exception as error:
        call_back({"event": "failed", "error": str(error) or type(error).__name__})
        return 0
    call_back({"event": "finished", "outcome": outcome})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
