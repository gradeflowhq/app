import secrets
import uuid
from datetime import timedelta
from typing import Self

from django.conf import settings
from django.db import models
from django.db.models import DurationField, ExpressionWrapper, F
from django.db.models.functions import Now

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.core_version import CORE_VERSION
from gradeflow_app.courses.models import Course


class JobQuerySet(models.QuerySet["Job"]):
    def running(self) -> Self:
        return self.filter(status__in=Job.ACTIVE).exclude(_timed_out())

    def timed_out(self) -> Self:
        """Jobs the sandbox has given up on: past its time limit since they started, or since they were handed to
        it without starting. They won't call back."""
        return self.filter(status__in=Job.ACTIVE).filter(_timed_out())


def _timed_out() -> models.Q:
    before = Now() - ExpressionWrapper((F("time_limit") + 60) * timedelta(seconds=1), output_field=DurationField())
    started = models.Q(status=Job.Status.RUNNING, started_at__lt=before)
    return started | models.Q(status=Job.Status.QUEUED, dispatched_at__lt=before)


def _time_limit() -> int:
    return settings.GRADEFLOW_JOB_TIME_LIMIT


class Job(models.Model):
    class Kind(models.TextChoices):
        REMARK = "remark"
        PREVIEW = "preview"
        GROUPING = "grouping"
        DRAFT_TESTS = "draft_tests"
        VERSION_TESTS = "version_tests"
        MODEL_CHOICE = "model_choice"
        MODEL_TRIAL = "model_trial"

    class Status(models.TextChoices):
        QUEUED = "queued"
        RUNNING = "running"
        COMPLETED = "completed"
        FAILED = "failed"

    ACTIVE = (Status.QUEUED, Status.RUNNING)

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    assessment = models.ForeignKey(Assessment, null=True, on_delete=models.CASCADE, related_name="jobs")
    course = models.ForeignKey(Course, null=True, on_delete=models.CASCADE, related_name="jobs")
    """None for tests of a person's own method."""
    marking = models.ForeignKey("marking.Marking", null=True, on_delete=models.SET_NULL, related_name="jobs")
    method_draft = models.ForeignKey(
        "libraries.MethodDraftRecord", null=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    version_tests = models.ForeignKey(
        "libraries.VersionTests", null=True, on_delete=models.SET_NULL, related_name="jobs"
    )
    revision = models.PositiveIntegerField(null=True)
    core_version = models.CharField(max_length=128, blank=True)
    """The core version that ran it, for tracing results."""
    image = models.CharField(max_length=512, blank=True)
    marked_with = models.JSONField(default=dict)
    """What it marked with, kept after its input is let go: the markings and methods, or a comparison's key."""
    kind = models.CharField(max_length=20, choices=Kind.choices)
    question_keys = models.JSONField(default=list)
    """Keys of the questions the job marks."""
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    progress = models.FloatField(default=0)
    time_limit = models.PositiveIntegerField(default=_time_limit)
    """Seconds the sandbox may run it."""
    progress_text = models.CharField(max_length=200, blank=True)
    """What a running job is waiting on, such as "Deciding criteria: 12 of 30"."""
    error = models.TextField(blank=True)
    input = models.JSONField(null=True, blank=True)
    """What it runs on, captured when it starts: a marking payload, a test run, or texts to compare."""
    result = models.JSONField(null=True, blank=True)
    """A preview's or test run's results, or an answer comparison's parts until it completes."""
    secret = models.CharField(max_length=64, default=secrets.token_urlsafe, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    objects = JobQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.kind} {self.status}"

    @property
    def ran_on_this_core(self) -> bool:
        """Whether this version of the core ran it: another's results may not read or mark as this one's do."""
        return self.core_version == CORE_VERSION

    @property
    def is_active(self) -> bool:
        return self.status in self.ACTIVE


class SubmissionBatch(models.Model):
    """Results a running job sent in one callback, kept until it finishes."""

    job = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="batches")
    submissions = models.JSONField()

    class Meta:
        ordering = ("pk",)

    def __str__(self) -> str:
        return f"{len(self.submissions)} submissions"
