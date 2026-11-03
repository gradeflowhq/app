"""Why marks beside a marking may be out of date."""

from collections.abc import Iterable, Mapping

from gradeflow_core import Check

from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.models import Marking
from gradeflow_app.model_service.endpoints import asks_models


def out_of_date_notices(accepted: Mapping[Marking, Check], question_keys: Iterable[str]) -> list[str]:
    """Why the marks of these questions are from before: their marking, one of the `accepted` ones, failed or wasn't
    run. One that can't mark says why in its own column; one marking them now shows its progress."""
    keys = set(question_keys)
    messages = []
    for marking, check in accepted.items():
        if not keys & marking.questions_given_marks:
            continue
        latest_job = marking.jobs.filter(revision=marking.revision).first()
        if latest_job is None and asks_models([check]):
            messages.append(
                "Not marked again: the change came from a device without an AI service. The marks shown are "
                "from before."
            )
        elif latest_job is not None and latest_job.status == Job.Status.FAILED:
            messages.append(f"Marking failed: {latest_job.error} The marks shown are from before.")
    return messages


def asks_language_models(accepted: Mapping[Marking, Check], question_keys: Iterable[str]) -> bool:
    """Whether one of the `accepted` markings of these questions asks a language model, which needs a model
    service."""
    keys = set(question_keys)
    return asks_models([check for marking, check in accepted.items() if keys & marking.questions_given_marks])
