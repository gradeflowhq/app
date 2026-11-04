"""No models: the runner holds the runs. The released migrations still name `_time_limit`."""

from django.conf import settings


def _time_limit() -> int:
    return settings.GRADEFLOW_JOB_TIME_LIMIT
