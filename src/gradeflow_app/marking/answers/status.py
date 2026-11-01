"""Why marks beside a marking may be out of date."""

from collections.abc import Iterable, Mapping

from gradeflow_core import Check

from gradeflow_app.jobs.models import Job
from gradeflow_app.marking.models import Marking
from gradeflow_app.model_service.endpoints import asks_models


def notices(accepted: Mapping[Marking, Check], keys: Iterable[str]) -> list[str]:
    """Why the marks of these questions are from before: their marking, one of the `accepted` ones, failed, or is
    marking them now. One that can't mark says why in its own column."""
    selected = set(keys)
    messages = []
    for saved, check in accepted.items():
        if not selected & saved.outputs:
            continue
        latest = saved.jobs.filter(revision=saved.revision).first()
        if latest is None and asks_models([check]):
            messages.append(
                "Not marked again: the change came from a device without an AI service. The marks shown are "
                "from before."
            )
        elif latest is not None and latest.status == Job.Status.FAILED:
            messages.append(f"Marking failed: {latest.error} The marks shown are from before.")
        elif latest is not None and latest.active:
            doing = f" {latest.step}." if latest.step else ""
            messages.append(f"Marking…{doing} The marks shown are from before.")
    return messages


def marked_by_models(accepted: Mapping[Marking, Check], keys: Iterable[str]) -> bool:
    """Whether one of the `accepted` markings of these questions asks a language model, which needs a model
    service."""
    selected = set(keys)
    return asks_models([check for saved, check in accepted.items() if selected & saved.outputs])
