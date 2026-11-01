"""Runs one GradeFlow job: marks submissions or test answers with the core, compares answers for grouping by
meaning, or chooses or tries a model endpoint's models.

Usage: sandbox.py mark|compare|test|choose|try SECONDS. It fetches input and sends signed callbacks as it goes, and
stops after SECONDS.
"""

import ctypes
import hashlib
import hmac
import json
import os
import signal
import sys
import time
from collections.abc import Callable
from typing import Any

import httpx

URL = os.environ["GRADEFLOW_JOB_URL"]
SECRET = os.environ["GRADEFLOW_JOB_SECRET"].encode()
BATCH_BYTES = 1_000_000
"""Target size for callbacks with results."""
REPORT_SECONDS = 1
"""Longest wait between callbacks with marked results, which show the job's progress."""
CHUNK_SIZE = 60
"""Students marked between callbacks when a method asks a language model about many answers at once."""
PR_SET_DUMPABLE = 4


def signature(body: bytes) -> str:
    return hmac.new(SECRET, body, hashlib.sha256).hexdigest()


def call_back(event: dict[str, Any]) -> None:
    body = json.dumps(event).encode()
    headers = {"Content-Type": "application/json", "X-GradeFlow-Signature": signature(body)}
    httpx.post(URL + "callback", content=body, headers=headers, timeout=120).raise_for_status()


def endpoint(job: dict[str, Any]) -> Any:
    """The model endpoint the job asks language models at, if any."""
    from gradeflow_core import ModelEndpoint

    found = job.get("model_endpoint")
    return ModelEndpoint.model_validate(found) if found else None


class Reporter:
    """Says how far a step that waits on a language model has got, about once a second."""

    def __init__(self) -> None:
        self.last = 0.0

    def __call__(self, progress: Any) -> None:
        now = time.monotonic()
        if progress.done < progress.total and now - self.last < REPORT_SECONDS:
            return
        self.last = now
        call_back({"event": "progress", "done": progress.done, "total": progress.total, "step": progress.step})


def mark(job: dict[str, Any]) -> None:
    from gradeflow_core import BUILTINS, MarkScheme, QuestionSet, Submission
    from gradeflow_core import mark as mark_submissions

    submissions = mark_submissions(
        QuestionSet.model_validate(job["questions"]),
        MarkScheme.load(job["markings"], methods=BUILTINS.with_files(job["methods"])),
        [Submission.model_validate(raw) for raw in job["submissions"]],
        model_endpoint=endpoint(job),
        on_progress=Reporter(),
        chunk_size=CHUNK_SIZE,
    )
    batch: list[dict[str, Any]] = []
    size, reported = 0, time.monotonic()
    for submission in submissions:
        item = submission.model_dump(mode="json")
        length = len(json.dumps(item))
        if batch and (size + length > BATCH_BYTES or time.monotonic() - reported >= REPORT_SECONDS):
            call_back({"event": "marked", "submissions": batch})
            batch, size, reported = [], 0, time.monotonic()
        batch.append(item)
        size += length
    if batch:
        call_back({"event": "marked", "submissions": batch})


def compare(job: dict[str, Any]) -> None:
    from gradeflow_core import compare_answers

    comparison = compare_answers(job["texts"], similar="meaning")
    payload = json.dumps(comparison.model_dump(mode="json"), ensure_ascii=True)
    # ASCII pieces leave room for JSON escaping and the callback envelope.
    size = BATCH_BYTES // 4
    total = (len(payload) + size - 1) // size
    for index, start in enumerate(range(0, len(payload), size)):
        call_back({"event": "compared", "index": index, "total": total, "part": payload[start : start + size]})


def test(job: dict[str, Any]) -> None:
    from gradeflow_core import BUILTINS, QuestionSet
    from gradeflow_core.editing import ExampleRow, evaluate_rows

    results = evaluate_rows(
        job["method"],
        methods=BUILTINS.with_files(job["methods"]),
        questions=QuestionSet.model_validate(job["questions"]),
        question=job["question"],
        inputs=job["inputs"],
        rows=[ExampleRow.model_validate(row) for row in job["rows"]],
        model_endpoint=endpoint(job),
    )
    call_back({"event": "tested", "results": [result.model_dump(mode="json") for result in results]})


def choose(job: dict[str, Any]) -> None:
    from gradeflow_core import choose_models

    choice = choose_models(endpoint(job))
    chosen = choice.endpoint
    call_back(
        {
            "event": "chosen",
            "choice": {
                "decision_model": chosen.decision_model,
                "reasoning_model": chosen.reasoning_model,
                "trials": [trial.model_dump(mode="json") for trial in choice.trials],
            },
        }
    )


def try_models(job: dict[str, Any]) -> None:
    from gradeflow_core.editing import try_model

    model_endpoint = endpoint(job)
    trials = [try_model(model_endpoint, each["role"], each["model"]) for each in job["tries"]]
    call_back({"event": "tried", "trials": [trial.model_dump(mode="json") for trial in trials]})


TASKS: dict[str, Callable[[dict[str, Any]], None]] = {
    "mark": mark,
    "compare": compare,
    "test": test,
    "choose": choose,
    "try": try_models,
}


def undumpable() -> None:
    """Keeps code the job runs, as the same user, from reading the job's secret from this process's memory or
    environment."""
    if ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE) failed")


def stopped(*_: object) -> None:
    """Reports the job failed at its time limit, or when Nomad stops it, so the app stops waiting for it."""
    call_back({"event": "failed", "error": "Took too long."})
    sys.exit(1)


def main(task: str, time_limit: int) -> int:
    undumpable()
    signal.signal(signal.SIGTERM, stopped)
    signal.signal(signal.SIGALRM, stopped)
    signal.alarm(time_limit)
    call_back({"event": "started"})
    response = httpx.get(URL + "input", headers={"X-GradeFlow-Signature": signature(b"input")}, timeout=120)
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
