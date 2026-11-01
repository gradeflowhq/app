import uuid

from django.db import models
from gradeflow_core import Question as CoreQuestion
from gradeflow_core.editing import QUESTION_TYPES, QuestionType
from simple_history.models import HistoricalRecords

from gradeflow_app.assessments.models import Assessment

TYPE_LABELS: dict[QuestionType, str] = {kind: question.label for kind, question in QUESTION_TYPES.items()}
"""Each question type's name, in the core's order."""


class Question(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="questions")
    key = models.CharField(max_length=255)
    """The core's question ID: the answer column's name."""
    position = models.PositiveIntegerField()
    definition = models.JSONField()
    history = HistoricalRecords()

    _core: tuple[object, CoreQuestion] | None = None
    """The core's question and the definition it was validated from, as pages read it once per student."""

    class Meta:
        constraints = (models.UniqueConstraint(fields=("assessment", "key"), name="one_question_per_key"),)
        ordering = ("position",)

    def __str__(self) -> str:
        return self.key

    @property
    def core(self) -> CoreQuestion:
        if self._core is None or self._core[0] is not self.definition:
            self._core = (self.definition, CoreQuestion.load(self.definition))
        return self._core[1]

    @core.setter
    def core(self, question: CoreQuestion) -> None:
        self.definition = question.model_dump(mode="json")
        self._core = (self.definition, question)
