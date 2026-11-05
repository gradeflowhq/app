import uuid
from dataclasses import dataclass
from functools import cached_property
from typing import TYPE_CHECKING
from uuid import UUID

from django.db import models
from django.shortcuts import get_object_or_404
from gradeflow_core import QuestionSet
from simple_history.models import HistoricalRecords

from gradeflow_app.courses.keys import MemberCourseKey, member_course_key
from gradeflow_app.courses.models import Course, Member

if TYPE_CHECKING:
    from gradeflow_app.questions.models import Question


class Assessment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="assessments")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    history = HistoricalRecords(excluded_fields=["updated_at"])

    class Meta:
        ordering = ("-updated_at",)

    def __str__(self) -> str:
        return self.name

    def lock(self) -> None:
        """Take the lock that orders changes to the assessment's answers, markings and results."""
        Assessment.objects.select_for_update().get(pk=self.pk)

    def question_set(self) -> QuestionSet:
        return QuestionSet({question.key: question.core_question for question in self.questions.all()})


@dataclass(frozen=True)
class AssessmentAccess:
    """A course member's access to one of its assessments: what every assessment view is given."""

    member: Member
    assessment: Assessment

    @property
    def course(self) -> Course:
        return self.assessment.course

    @property
    def can_edit(self) -> bool:
        return self.member.can_edit

    @property
    def is_owner(self) -> bool:
        return self.member.is_owner

    @cached_property
    def course_key(self) -> MemberCourseKey:
        return member_course_key(self.member)

    def question(self, question_id: UUID) -> Question:
        """One of the assessment's questions; "not found" for any other."""
        return get_object_or_404(self.assessment.questions, pk=question_id)
