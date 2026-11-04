"""Runs one GradeFlow job: marks submissions or test answers with the core, compares answers for grouping by
meaning, or chooses or tries a model endpoint's models.

Usage: sandbox.py mark|compare|test|choose|trial SECONDS. It fetches input and sends signed callbacks as it goes, and
stops after SECONDS.
"""

import ctypes
import dataclasses
import hashlib
import hmac
import json
import os
import random
import signal
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

JOB_URL = os.environ["GRADEFLOW_JOB_URL"]
JOB_SECRET = os.environ["GRADEFLOW_JOB_SECRET"].encode()
MAX_BATCH_BYTES = 1_000_000
"""Target size for callbacks with results."""
MAX_SECONDS_BETWEEN_CALLBACKS = 1
"""Longest wait between callbacks with marked results, which show the job's progress."""
CHUNK_SIZE = 60
"""Students marked between callbacks when a method asks a language model about many answers at once."""
CALLBACK_ATTEMPTS = 6
"""Tries for each callback, spread over up to a minute, so jobs outlast the app restarting. The app takes each event
once, however often it's sent."""
PR_SET_DUMPABLE = 4
CLIENT = httpx.Client(timeout=120)


def signature(body: bytes) -> str:
    return hmac.new(JOB_SECRET, body, hashlib.sha256).hexdigest()


def call_back(event: dict[str, Any], *, attempts: int = CALLBACK_ATTEMPTS) -> None:
    """Sends `event`, trying again after a while when the app can't be reached, fails or is busy."""
    body = json.dumps(event).encode()
    headers = {"Content-Type": "application/json", "X-GradeFlow-Signature": signature(body)}
    for attempt in range(1, attempts + 1):
        try:
            response = CLIENT.post(JOB_URL + "callback", content=body, headers=headers)
            if attempt == attempts or not (response.is_server_error or response.status_code == 429):
                response.raise_for_status()
                return
        except httpx.TransportError:
            if attempt == attempts:
                raise
        time.sleep(random.uniform(0, 2**attempt))


def model_endpoint(job: dict[str, Any]) -> Any:
    """The model endpoint the job asks language models at, if any."""
    from gradeflow_core import ModelEndpoint

    endpoint_json = job.get("model_endpoint")
    return ModelEndpoint.model_validate(endpoint_json) if endpoint_json else None


class ProgressReporter:
    """Says how far a step that waits on a language model has got, about once a second."""

    def __init__(self) -> None:
        self.last = 0.0

    def __call__(self, progress: Any) -> None:
        now = time.monotonic()
        if progress.completed < progress.total and now - self.last < MAX_SECONDS_BETWEEN_CALLBACKS:
            return
        self.last = now
        call_back({"event": "progress", **dataclasses.asdict(progress)})


def mark(job: dict[str, Any]) -> None:
    from gradeflow_core import BUILTINS, MarkScheme, QuestionSet, Submission
    from gradeflow_core import mark as mark_submissions

    submissions = mark_submissions(
        QuestionSet.model_validate(job["questions"]),
        MarkScheme.load(job["markings"], methods=BUILTINS.with_files(job["methods"])),
        [Submission.model_validate(raw) for raw in job["submissions"]],
        model_endpoint=model_endpoint(job),
        on_progress=ProgressReporter(),
        chunk_size=CHUNK_SIZE,
    )
    batch: list[dict[str, Any]] = []
    size, last_callback_at = 0, time.monotonic()
    for submission in submissions:
        submission_json = submission.model_dump(mode="json")
        length = len(json.dumps(submission_json))
        if batch and (
            size + length > MAX_BATCH_BYTES or time.monotonic() - last_callback_at >= MAX_SECONDS_BETWEEN_CALLBACKS
        ):
            call_back({"event": "marked", "submissions": batch})
            batch, size, last_callback_at = [], 0, time.monotonic()
        batch.append(submission_json)
        size += length
    if batch:
        call_back({"event": "marked", "submissions": batch})


def compare(job: dict[str, Any]) -> None:
    from gradeflow_core import compare_answers

    comparison = compare_answers(job["texts"], similarity="meaning")
    comparison_json = json.dumps(comparison.model_dump(mode="json"), ensure_ascii=True)
    # ASCII pieces leave room for JSON escaping and the callback envelope.
    size = MAX_BATCH_BYTES // 4
    parts = [comparison_json[start : start + size] for start in range(0, len(comparison_json), size)]
    for index, text in enumerate(parts):
        call_back({"event": "compared", "index": index, "total": len(parts), "text": text})


def test(job: dict[str, Any]) -> None:
    from gradeflow_core import BUILTINS, QuestionSet
    from gradeflow_core.editing import ExampleRow, mark_rows

    results = mark_rows(
        job["method"],
        methods=BUILTINS.with_files(job["methods"]),
        questions=QuestionSet.model_validate(job["questions"]),
        question=job["question"],
        inputs=job["inputs"],
        rows=[ExampleRow.model_validate(row) for row in job["rows"]],
        model_endpoint=model_endpoint(job),
    )
    call_back({"event": "tested", "results": [result.model_dump(mode="json") for result in results]})


def choose(job: dict[str, Any]) -> None:
    from gradeflow_core import choose_models

    choice = choose_models(model_endpoint(job))
    chosen_endpoint = choice.endpoint
    call_back(
        {
            "event": "chosen",
            "choice": {
                "fast_model": chosen_endpoint.fast_model,
                "careful_model": chosen_endpoint.careful_model,
                "trials": [trial.model_dump(mode="json") for trial in choice.trials],
            },
        }
    )


def try_models(job: dict[str, Any]) -> None:
    from gradeflow_core.editing import try_model

    endpoint = model_endpoint(job)
    trials = [try_model(endpoint, trial["role"], trial["model"]) for trial in job["trials"]]
    call_back({"event": "trials", "trials": [trial.model_dump(mode="json") for trial in trials]})


TASKS: dict[str, Callable[[dict[str, Any]], None]] = {
    "mark": mark,
    "compare": compare,
    "test": test,
    "choose": choose,
    "trial": try_models,
}


def hide_secret_from_job_code() -> None:
    """Keeps code the job runs, as the same user, from reading the job's secret from this process's memory or
    environment."""
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")


def report_time_limit(*_: object) -> None:
    """Reports the job failed at its time limit, or when Nomad stops it, so the app stops waiting for it. It has
    seconds before Nomad kills it, so it tries once."""
    call_back({"event": "failed", "error": "Ran past its time limit."}, attempts=1)
    sys.exit(1)


def main(task: str, time_limit: int) -> int:
    hide_secret_from_job_code()
    signal.signal(signal.SIGTERM, report_time_limit)
    signal.signal(signal.SIGALRM, report_time_limit)
    signal.alarm(time_limit)
    call_back({"event": "started"})
    response = CLIENT.get(JOB_URL + "input", headers={"X-GradeFlow-Signature": signature(b"input")})
    response.raise_for_status()
    try:
        TASKS[task](response.json())
    except Exception as error:
        call_back({"event": "failed", "error": str(error) or type(error).__name__})
        return 1
    call_back({"event": "finished"})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], int(sys.argv[2])))
