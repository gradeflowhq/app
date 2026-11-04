from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from gradeflow_core import ModelEndpoint, ModelTrial, best_models

from gradeflow_app.accounts.models import User
from gradeflow_app.jobs.models import Job

MODEL_NAME_LENGTH = 200
ANSWERS_PER_REQUEST_LIMITS = ModelEndpoint.model_json_schema()["properties"]["answers_per_request"]
"""The core's default, minimum and maximum of how many students' answers one request may hold."""


class ModelService(models.Model):
    """A person's use of one of the server's AI services: the models marking asks and how they were chosen. Each
    device keeps its own token."""

    class ChosenBy(models.TextChoices):
        NOT_YET = ""
        AUTOMATICALLY = "automatically"
        MYSELF = "myself"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="model_services")
    url = models.URLField()
    chosen_by = models.CharField(max_length=20, choices=ChosenBy, blank=True)
    fast_model = models.CharField(max_length=MODEL_NAME_LENGTH, blank=True)
    careful_model = models.CharField(max_length=MODEL_NAME_LENGTH, blank=True)
    """The models picked; choosing automatically uses the best tested instead."""
    answers_per_request = models.PositiveSmallIntegerField(
        default=ANSWERS_PER_REQUEST_LIMITS["default"],
        validators=[
            MinValueValidator(ANSWERS_PER_REQUEST_LIMITS["minimum"]),
            MaxValueValidator(ANSWERS_PER_REQUEST_LIMITS["maximum"]),
        ],
    )

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "url"], name="one_model_service_per_url")]

    def __str__(self) -> str:
        return self.url

    def trials(self) -> list[ModelTrial]:
        return [test.trial() for test in self.tests.exclude(seconds=None)]

    def models_in_use(self) -> tuple[str, str]:
        """The fast and the careful model marking asks; empty where there's none."""
        if self.chosen_by != self.ChosenBy.AUTOMATICALLY:
            return self.fast_model, self.careful_model
        fast_model, careful_model = best_models(self.trials())
        return fast_model or "", careful_model or ""


class ModelTest(models.Model):
    """How a model did in one role when last tested, if it has been, and whether a job is testing it now."""

    class Role(models.TextChoices):
        FAST = "fast"
        CAREFUL = "careful"

    service = models.ForeignKey(ModelService, on_delete=models.CASCADE, related_name="tests")
    model = models.CharField(max_length=MODEL_NAME_LENGTH)
    role = models.CharField(max_length=10, choices=Role)
    rows_matched = models.PositiveIntegerField(null=True)
    rows_tried = models.PositiveIntegerField(null=True)
    seconds = models.FloatField(null=True)
    failure = models.TextField(blank=True)
    job = models.ForeignKey(Job, on_delete=models.SET_NULL, null=True, related_name="+")
    """The job testing it, or that last did."""
    testing = models.BooleanField(default=False)
    """Whether `job` has yet to report it."""

    class Meta:
        constraints = [models.UniqueConstraint(fields=["service", "model", "role"], name="one_test_per_model_role")]

    def __str__(self) -> str:
        return f"{self.model} as {self.role}"

    def trial(self) -> ModelTrial:
        return ModelTrial.model_validate(
            {
                "model": self.model,
                "role": self.role,
                "rows_matched": self.rows_matched,
                "rows_tried": self.rows_tried,
                "seconds": self.seconds,
                "failure": self.failure or None,
            }
        )
