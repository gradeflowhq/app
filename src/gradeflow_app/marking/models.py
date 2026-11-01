import uuid
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from django.conf import settings
from django.db import models, transaction
from gradeflow_core.editing import methods_used
from simple_history.models import HistoricalRecords

from gradeflow_app.accounts.models import User
from gradeflow_app.assessments.models import Assessment
from gradeflow_app.libraries.models import MethodVersion
from gradeflow_app.marking.checks import tree_of
from gradeflow_app.questions.models import Question


class Marking(models.Model):
    """A question's marking, or a question group's when it has no question: the core's check, as YAML."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="markings")
    question = models.OneToOneField(Question, null=True, blank=True, on_delete=models.CASCADE, related_name="marking")
    definition = models.TextField()
    uses = models.ManyToManyField(MethodVersion, through="MarkingUse", related_name="markings")
    """Every saved version it needs, directly or through others."""
    revision = models.PositiveIntegerField(default=0)
    """Counts its remarks; marks and jobs of an earlier one are out of date."""
    output_keys = models.JSONField(default=list)
    """The questions it marked when it last could, kept while it can't so they stay its own."""
    input_keys = models.JSONField(default=list)
    """The questions it read then: a change to one remarks it."""
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords()

    class Meta:
        ordering = ("id",)

    def __str__(self) -> str:
        return self.question.key if self.question else "Question group"

    @transaction.atomic
    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        if "definition" in (kwargs.get("update_fields") or ["definition"]):
            self.uses.set(MethodVersion.objects.linked(methods_used(tree_of(self.definition))))

    @property
    def outputs(self) -> set[str]:
        """The questions it gives marks to."""
        return set(self.output_keys) | ({self.question.key} if self.question else set())


class Draft(models.Model):
    """A user's unsaved marking of a question, or of a question group, new or saved."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="+")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    question = models.ForeignKey(Question, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    marking = models.ForeignKey(Marking, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    definition = models.TextField()
    based_on = models.DateTimeField(null=True, blank=True)
    """When the saved marking this draft started from was saved; none for new marking."""
    preview = models.ForeignKey("jobs.Job", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    """Its latest preview since editing started, kept here rather than in the session: tabs share a session, and
    each request saves it whole."""
    previewed = models.TextField(null=True, blank=True)  # noqa: DJ001 - null is "never previewed"
    """What `preview` marked with: the definition and the questions it marked or read, as they were."""

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=("user", "question"), condition=models.Q(question__isnull=False), name="one_draft_per_question"
            ),
            models.UniqueConstraint(
                fields=("user", "marking"), condition=models.Q(marking__isnull=False), name="one_draft_per_marking"
            ),
        )

    def __str__(self) -> str:
        return f"Draft by {self.user}"

    @cached_property
    def subject(self) -> Subject:
        return Subject.of(self.assessment, self.question, self.marking)


@dataclass(frozen=True)
class Subject:
    """What a marking marks: a question, or a question group when there is no `question`. `marking` is the saved
    marking, None until there is one."""

    assessment: Assessment
    question: Question | None
    marking: Marking | None

    @classmethod
    def of(cls, assessment: Assessment, question: Question | None = None, marking: Marking | None = None) -> Subject:
        """The subject of a question's marking, looked up by `question`, or of the question group `marking` is;
        with neither, of a new question group."""
        if question is not None:
            marking = Marking.objects.filter(question=question).first()
        return cls(assessment, question, marking)

    @property
    def key(self) -> str | None:
        """The question's key, as the core names it; None for a question group."""
        return self.question.key if self.question else None

    def draft_of(self, user: User, *, lock: bool = False) -> Draft | None:
        """The user's draft of this marking; a new question group is known only by its draft."""
        drafts = Draft.objects.filter(user=user)
        if lock:
            drafts = drafts.select_for_update()
        if self.question is not None:
            return drafts.filter(question=self.question).first()
        if self.marking is not None:
            return drafts.filter(marking=self.marking).first()
        return None


class MarkingUse(models.Model):
    """A version a marking needs, which can't be deleted while it does."""

    marking = models.ForeignKey(Marking, on_delete=models.CASCADE, related_name="+")
    version = models.ForeignKey(MethodVersion, on_delete=models.RESTRICT, related_name="+")

    class Meta:
        constraints = (models.UniqueConstraint(fields=("marking", "version"), name="one_marking_use"),)

    def __str__(self) -> str:
        return f"{self.marking_id} uses {self.version_id}"
