from typing import Self

from django.conf import settings
from django.db import models

from gradeflow_app.assessments.models import Assessment
from gradeflow_app.courses.models import Course


class MarksMode(models.TextChoices):
    MARKS = "marks", "Marks"
    PERCENT = "percent", "Percentage"


class CourseCanvas(models.Model):
    """The Canvas course a course's marks go to, and how they're sent unless an assessment says otherwise."""

    course = models.OneToOneField(Course, primary_key=True, on_delete=models.CASCADE, related_name="canvas")
    canvas_course = models.CharField(max_length=50, blank=True)
    canvas_course_name = models.CharField(max_length=255, blank=True)
    canvas_assignment_group = models.CharField(max_length=50, blank=True)
    """The Canvas assignment group new assignments go in; "new" makes one called GradeFlow."""
    canvas_assignment_group_name = models.CharField(max_length=255, blank=True)
    marks_mode = models.CharField(max_length=10, choices=MarksMode.choices, default=MarksMode.MARKS)
    rounding = models.FloatField(null=True, blank=True)
    """Round marks sent to Canvas to the nearest multiple of this, if set."""
    include_feedback = models.BooleanField(default=False)

    def __str__(self) -> str:
        return self.canvas_course_name or self.canvas_course

    @classmethod
    def of(cls, course: Course) -> Self:
        """The course's, or unsaved defaults if it has none."""
        return cls.objects.filter(course=course).first() or cls(course=course)


class AssessmentCanvas(models.Model):
    """The Canvas assignment an assessment's marks go to, and how they're sent where that differs from its course:
    a blank `marks_mode`, `rounding` or `include_feedback` sends them as the course does."""

    assessment = models.OneToOneField(Assessment, primary_key=True, on_delete=models.CASCADE, related_name="canvas")
    canvas_assignment = models.CharField(max_length=50, blank=True)
    marks_mode = models.CharField(max_length=10, choices=MarksMode.choices, blank=True)
    rounding = models.FloatField(null=True, blank=True)
    include_feedback = models.BooleanField(null=True, blank=True)

    def __str__(self) -> str:
        return self.canvas_assignment

    @classmethod
    def of(cls, assessment: Assessment) -> Self:
        """The assessment's, or unsaved defaults if it has none."""
        return cls.objects.filter(assessment=assessment).first() or cls(assessment=assessment)


class SentMarks(models.Model):
    assessment = models.ForeignKey(Assessment, on_delete=models.CASCADE, related_name="sent_marks")
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    sent_at = models.DateTimeField(auto_now_add=True)
    assignment_name = models.CharField(max_length=255)
    student_count = models.PositiveIntegerField()

    class Meta:
        ordering = ("-sent_at",)

    def __str__(self) -> str:
        return self.assignment_name
