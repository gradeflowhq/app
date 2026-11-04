from django.db import models
from gradeflow_core import CheckResult, QuestionResult
from pydantic import ValidationError
from simple_history.models import HistoricalRecords

from gradeflow_app.imports.models import Student
from gradeflow_app.questions.models import Question


class Result(models.Model):
    """What a question's marking gives a student: the core's result, with the proportion of the question's marks
    from 0 to 1."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="results")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="results")
    proportion = models.FloatField()
    feedback = models.TextField(blank=True)
    method_label = models.TextField()
    is_marked = models.BooleanField(default=True)
    model_names = models.JSONField(default=list)
    """The language models that decided it, if any."""
    breakdown = models.JSONField(null=True)
    """How each check of the marking marked it, if the marking has several."""
    marking = models.ForeignKey("marking.Marking", null=True, on_delete=models.SET_NULL, related_name="results")
    revision = models.PositiveIntegerField()

    class Meta:
        constraints = (models.UniqueConstraint(fields=("student", "question"), name="one_result_per_student_question"),)

    def __str__(self) -> str:
        return f"{self.proportion:.0%}"

    def question_result(self, max_marks: float) -> QuestionResult:
        """The core's result, out of the question's `max_marks` as they are now; without the breakdown if an earlier
        core stored one this core can't read."""
        try:
            breakdown = CheckResult.model_validate(self.breakdown) if self.breakdown is not None else None
        except ValidationError:
            breakdown = None
        return QuestionResult(
            proportion=self.proportion,
            max_marks=max_marks,
            feedback=self.feedback,
            method_label=self.method_label,
            is_marked=self.is_marked,
            model_names=tuple(self.model_names),
            breakdown=breakdown,
        )


class Adjustment(models.Model):
    """Marks or feedback a person set for a student's answer in place of the marking's. Unlike a `Result`, it is
    kept when the question is marked again."""

    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="adjustments")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="adjustments")
    marks = models.FloatField(null=True, blank=True)
    feedback = models.TextField(null=True, blank=True)  # noqa: DJ001 - null keeps the marking's feedback
    history = HistoricalRecords()

    class Meta:
        constraints = (
            models.UniqueConstraint(fields=("student", "question"), name="one_adjustment_per_student_question"),
        )

    def __str__(self) -> str:
        return f"{self.marks}"
