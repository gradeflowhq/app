"""Follow the runner's runs."""

import logging
import time
from typing import Any

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from gradeflow_app.jobs import runs
from gradeflow_app.jobs.runner import Ending, Run, RunnerUnavailable

_LOG = logging.getLogger(__name__)
RETRY_SECONDS = 5


class _Listener:
    """`runs.listener` on a fresh database connection for each change, since changes can be minutes apart."""

    def changed(self, run: Run) -> None:
        close_old_connections()
        runs.listener.changed(run)

    def ended(self, run: Run, ending: Ending) -> None:
        close_old_connections()
        runs.listener.ended(run, ending)


class Command(BaseCommand):
    help = "Tell pages as runs start and end, and fail those that end without calling back."

    def handle(self, *args: Any, **options: Any) -> None:
        while True:
            try:
                runs.runner().follow(_Listener())
            except RunnerUnavailable:
                _LOG.warning("The runner is unavailable", exc_info=True)
                time.sleep(RETRY_SECONDS)
