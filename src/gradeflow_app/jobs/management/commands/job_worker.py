"""Recover and dispatch persisted sandbox jobs after application restarts."""

import time
from typing import Any

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from gradeflow_app.jobs.queue import recover_queue


class Command(BaseCommand):
    help = "Dispatch queued sandbox jobs and recover interrupted ones."

    def handle(self, *args: Any, **options: Any) -> None:
        while True:
            close_old_connections()
            recover_queue()
            time.sleep(5)
